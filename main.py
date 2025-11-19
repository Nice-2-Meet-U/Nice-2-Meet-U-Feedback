from __future__ import annotations

import os, socket, base64, logging, json, hashlib
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from urllib.parse import urlencode
from uuid import UUID, uuid4

# NEW: load environment variables from .env
from dotenv import load_dotenv
load_dotenv()

import mysql.connector
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, status, Response, Header, Request
from fastapi import Query, Path
import uvicorn
from models.health import Health
from models.profile_feedback import (
    ProfileFeedbackCreate, ProfileFeedbackOut, ProfileFeedbackUpdate
)
from models.app_feedback import (
    AppFeedbackCreate, AppFeedbackOut, AppFeedbackUpdate
)

# -------------------------------------------------------------------
# Config / DB helpers
# -------------------------------------------------------------------
port = int(os.environ.get("FASTAPIPORT", 8000))
DB_CFG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
}

# Optional: fail fast if any required env is missing
_required = ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"]
_missing = [k for k in _required if not os.getenv(k)]
if _missing:
    raise RuntimeError(f"Missing required env vars: {', '.join(_missing)}. Check your .env file.")

def db() -> mysql.connector.MySQLConnection:
    return mysql.connector.connect(**DB_CFG)


def run(sql: str, params: tuple = (), fetch: str | None = None):
    """
    Execute SQL. fetch=None (no results), 'one' (single row), 'all' (all rows).
    Returns (rows or None).
    """
    conn = db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        rows = None
        if fetch == "one":
            rows = cur.fetchone()
        elif fetch == "all":
            rows = cur.fetchall()
        conn.commit()
        cur.close()
        return rows
    finally:
        conn.close()

def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()

def decode_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor")

def make_etag(row: dict) -> str:
    """Generate a deterministic strong ETag from id + updated_at."""
    updated = row.get("updated_at")
    if isinstance(updated, datetime):
        stamp = updated.isoformat(timespec="microseconds")
    else:
        stamp = str(updated)
    payload = f"{row.get('id')}|{stamp}"
    digest = hashlib.sha1(payload.encode()).hexdigest()
    return f'"{digest}"'

def parse_etag_header(header_value: Optional[str]) -> List[str]:
    if not header_value:
        return []
    return [part.strip() for part in header_value.split(",") if part.strip()]

QueryItems = List[Tuple[str, str]]

def ensure_relative_path(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"

def make_relative_url(path: str, items: Optional[QueryItems] = None) -> str:
    relative_path = ensure_relative_path(path)
    if not items:
        return relative_path
    query = urlencode(items, doseq=True)
    return f"{relative_path}?{query}" if query else relative_path

def query_items_from_request(request: Request) -> QueryItems:
    return [(k, v) for k, v in request.query_params.multi_items()]

def override_query_items(items: QueryItems, overrides: Dict[str, Optional[str]]) -> QueryItems:
    skip = set(overrides.keys())
    updated = [(k, v) for (k, v) in items if k not in skip]
    for key, value in overrides.items():
        if value is None:
            continue
        updated.append((key, str(value)))
    return updated

def build_profile_links(row: dict) -> Dict[str, str]:
    resource_id = str(row["id"])
    reviewer_id = str(row["reviewer_profile_id"])
    reviewee_id = str(row["reviewee_profile_id"])
    match_id = row.get("match_id")
    links: Dict[str, str] = {
        "self": make_relative_url(f"/feedback/profile/{resource_id}"),
        "collection": ensure_relative_path("/feedback/profile"),
        "reviewee_feedback": make_relative_url(
            "/feedback/profile", [("reviewee_profile_id", reviewee_id)]
        ),
        "reviewer_feedback": make_relative_url(
            "/feedback/profile", [("reviewer_profile_id", reviewer_id)]
        ),
        "stats": make_relative_url(
            "/feedback/profile/stats", [("reviewee_profile_id", reviewee_id)]
        ),
    }
    if match_id:
        links["match_feedback"] = make_relative_url(
            "/feedback/profile", [("match_id", str(match_id))]
        )
    return links

def build_app_links(row: dict) -> Dict[str, str]:
    resource_id = str(row["id"])
    author_id = row.get("author_profile_id")
    links: Dict[str, str] = {
        "self": make_relative_url(f"/feedback/app/{resource_id}"),
        "collection": ensure_relative_path("/feedback/app"),
        "stats": make_relative_url("/feedback/app/stats"),
    }
    if author_id:
        links["author_feedback"] = make_relative_url(
            "/feedback/app", [("author_profile_id", str(author_id))]
        )
    return links

def build_collection_links(
    path: str,
    base_items: QueryItems,
    *,
    next_cursor: Optional[str] = None,
    prev_cursor: Optional[str] = None,
    next_offset: Optional[int] = None,
    prev_offset: Optional[int] = None,
) -> Dict[str, str]:
    links: Dict[str, str] = {
        "self": make_relative_url(path, base_items),
        "collection": ensure_relative_path(path),
    }
    if next_cursor:
        links["next"] = make_relative_url(
            path, override_query_items(base_items, {"cursor": next_cursor, "offset": None})
        )
    elif next_offset is not None:
        links["next"] = make_relative_url(
            path, override_query_items(base_items, {"offset": str(next_offset), "cursor": None})
        )
    if prev_cursor:
        links["prev"] = make_relative_url(
            path, override_query_items(base_items, {"cursor": prev_cursor, "offset": None})
        )
    elif prev_offset is not None:
        links["prev"] = make_relative_url(
            path, override_query_items(base_items, {"offset": str(prev_offset), "cursor": None})
        )
    return links

# -------------------------------------------------------------------
# Schema bootstrap (id as CHAR(36), tags JSON)
# -------------------------------------------------------------------
PROFILE_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback_profile (
  id CHAR(36) PRIMARY KEY,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  reviewer_profile_id CHAR(36) NOT NULL,
  reviewee_profile_id CHAR(36) NOT NULL,
  match_id CHAR(36) NULL,

  overall_experience TINYINT NOT NULL,
  would_meet_again TINYINT NULL,
  safety_feeling TINYINT NULL,
  respectfulness TINYINT NULL,

  headline VARCHAR(120) NULL,
  comment TEXT NULL,
  tags JSON NULL,

  UNIQUE KEY uq_match_reviewer (match_id, reviewer_profile_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback_app (
  id CHAR(36) PRIMARY KEY,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  author_profile_id CHAR(36) NULL,

  overall TINYINT NOT NULL,
  usability TINYINT NULL,
  reliability TINYINT NULL,
  performance TINYINT NULL,
  support_experience TINYINT NULL,

  headline VARCHAR(120) NULL,
  comment TEXT NULL,
  tags JSON NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

logger = logging.getLogger("uvicorn.error")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables if needed and log DB reachability
    try:
        run(PROFILE_SCHEMA)
        run(APP_SCHEMA)
        run("SELECT 1", fetch="one")
        logger.info("DB startup check: OK")
    except Exception as e:
        logger.error(f"DB startup check: FAILED ({e})")
    yield

app = FastAPI(
    title="Feedback Microservice",
    version="1.0.0",
    lifespan=lifespan,
)

# allow whatever origins you expect (localhost:3000 in dev, prod domains later)
allowed_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in allowed_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------
# Health
# -------------------------------------------------------------------
def make_health(echo: Optional[str], path_echo: Optional[str]=None) -> Health:
    return Health(
        status=200,
        status_message="OK",
        timestamp=datetime.utcnow().isoformat() + "Z",
        ip_address=socket.gethostbyname(socket.gethostname()),
        echo=echo,
        path_echo=path_echo
    )

@app.get("/health", response_model=Health)
def get_health_no_path(echo: str | None = Query(None)):
    return make_health(echo=echo, path_echo=None)

@app.get("/health/{path_echo}", response_model=Health)
def get_health_with_path(
    path_echo: str = Path(...),
    echo: str | None = Query(None),
):
    return make_health(echo=echo, path_echo=path_echo)

# -------------------------------------------------------------------
# Mappers (DB row -> Pydantic)
# -------------------------------------------------------------------
def _coerce_tags(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            if isinstance(decoded, list):
                return [str(x) for x in decoded]
        except Exception:
            pass
        return [s.strip() for s in value.split(",") if s.strip()]
    return [str(value)]

def row_to_profile_out(r: dict) -> ProfileFeedbackOut:
    return ProfileFeedbackOut(
        id=UUID(r["id"]),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        reviewer_profile_id=UUID(r["reviewer_profile_id"]),
        reviewee_profile_id=UUID(r["reviewee_profile_id"]),
        match_id=UUID(r["match_id"]) if r["match_id"] else None,
        overall_experience=r["overall_experience"],
        would_meet_again=bool(r["would_meet_again"]) if r["would_meet_again"] is not None else None,
        safety_feeling=r["safety_feeling"],
        respectfulness=r["respectfulness"],
        headline=r["headline"],
        comment=r["comment"],
        tags=_coerce_tags(r["tags"]),
        links=build_profile_links(r),
    )

def row_to_app_out(r: dict) -> AppFeedbackOut:
    return AppFeedbackOut(
        id=UUID(r["id"]),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        author_profile_id=UUID(r["author_profile_id"]) if r["author_profile_id"] else None,
        overall=r["overall"],
        usability=r["usability"],
        reliability=r["reliability"],
        performance=r["performance"],
        support_experience=r["support_experience"],
        headline=r["headline"],
        comment=r["comment"],
        tags=_coerce_tags(r["tags"]),
        links=build_app_links(r),
    )

# -------------------------------------------------------------------
# PROFILE FEEDBACK (DB-backed)
# -------------------------------------------------------------------
@app.post("/feedback/profile", response_model=ProfileFeedbackOut, status_code=status.HTTP_201_CREATED)
def create_profile_feedback(payload: ProfileFeedbackCreate):
    now = datetime.utcnow()
    pid = str(uuid4())
    try:
        run(
            """
            INSERT INTO feedback_profile
            (id, created_at, updated_at, reviewer_profile_id, reviewee_profile_id, match_id,
             overall_experience, would_meet_again, safety_feeling, respectfulness,
             headline, comment, tags)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                pid, now, now,
                str(payload.reviewer_profile_id), str(payload.reviewee_profile_id),
                str(payload.match_id) if payload.match_id else None,
                payload.overall_experience, payload.would_meet_again,
                payload.safety_feeling, payload.respectfulness,
                payload.headline, payload.comment,
                None if payload.tags is None else json.dumps(payload.tags),
            ),
        )
    except mysql.connector.Error as e:
        # Duplicate for (match_id, reviewer) -> 409
        if e.errno in (1062,):  # duplicate key
            raise HTTPException(status_code=409, detail="Feedback already exists for this (match_id, reviewer)")
        raise
    row = run("SELECT * FROM feedback_profile WHERE id=%s", (pid,), fetch="one")
    return row_to_profile_out(row)

@app.get("/feedback/profile/{id}", response_model=ProfileFeedbackOut)
def get_profile_feedback(id: UUID = Path(...)):
    row = run("SELECT * FROM feedback_profile WHERE id=%s", (str(id),), fetch="one")
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return row_to_profile_out(row)

@app.patch("/feedback/profile/{id}", response_model=ProfileFeedbackOut)
def update_profile_feedback(payload: ProfileFeedbackUpdate, id: UUID = Path(...)):
    existing = run("SELECT * FROM feedback_profile WHERE id=%s", (str(id),), fetch="one")
    if not existing:
        raise HTTPException(status_code=404, detail="Not found")

    # Prepare updates dynamically
    fields, params = [], []
    def setf(col, val):
        fields.append(f"{col}=%s"); params.append(val)

    data = payload.model_dump(exclude_unset=True)
    # Potential uniqueness re-check: if match_id/reviewer_profile_id changes, MySQL unique key will enforce
    for key, col in [
        ("reviewer_profile_id", "reviewer_profile_id"),
        ("reviewee_profile_id", "reviewee_profile_id"),
        ("match_id", "match_id"),
        ("overall_experience", "overall_experience"),
        ("would_meet_again", "would_meet_again"),
        ("safety_feeling", "safety_feeling"),
        ("respectfulness", "respectfulness"),
        ("headline", "headline"),
        ("comment", "comment"),
    ]:
        if key in data:
            v = data[key]
            if key.endswith("_profile_id") or key == "match_id":
                v = str(v) if v is not None else None
            setf(col, v)

    if "tags" in data:
        tags = data["tags"]
        setf("tags", None if tags is None else json.dumps(tags))

    if not fields:
        # no-op, just return current row
        return row_to_profile_out(existing)

    params.append(str(id))
    sql = f"UPDATE feedback_profile SET {', '.join(fields)}, updated_at=NOW(6) WHERE id=%s"
    try:
        run(sql, tuple(params))
    except mysql.connector.Error as e:
        if e.errno in (1062,):
            raise HTTPException(status_code=409, detail="Feedback already exists for this (match_id, reviewer)")
        raise
    row = run("SELECT * FROM feedback_profile WHERE id=%s", (str(id),), fetch="one")
    return row_to_profile_out(row)

@app.delete("/feedback/profile/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile_feedback(id: UUID = Path(...)):
    res = run("DELETE FROM feedback_profile WHERE id=%s", (str(id),))
    return None

@app.get("/feedback/profile", response_model=Dict[str, object])
def list_profile_feedback(
    request: Request,
    reviewee_profile_id: Optional[UUID] = Query(default=None),
    reviewer_profile_id: Optional[UUID] = Query(default=None),
    match_id: Optional[UUID] = Query(default=None),
    tags: Optional[str] = Query(default=None, description="Comma-separated list; OR semantics"),
    min_overall: Optional[int] = Query(default=None, ge=1, le=5),
    max_overall: Optional[int] = Query(default=None, ge=1, le=5),
    since: Optional[datetime] = Query(default=None),
    search: Optional[str] = Query(
        default=None,
        min_length=1,
        description="Case-insensitive search across headline/comment content",
    ),
    sort: str = Query(default="created_at", pattern="^(created_at|overall_experience)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
):
    where, params = [], []
    if reviewee_profile_id: where.append("reviewee_profile_id=%s"); params.append(str(reviewee_profile_id))
    if reviewer_profile_id: where.append("reviewer_profile_id=%s"); params.append(str(reviewer_profile_id))
    if match_id:             where.append("match_id=%s");            params.append(str(match_id))
    if since:                where.append("created_at >= %s");       params.append(since)
    if min_overall is not None: where.append("overall_experience >= %s"); params.append(min_overall)
    if max_overall is not None: where.append("overall_experience <= %s"); params.append(max_overall)
    if tags:
        # Example builder for WHERE on MariaDB
        tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
        if tag_list:
            where.append("(" + " OR ".join(["JSON_SEARCH(tags, 'one', %s) IS NOT NULL"] * len(tag_list)) + ")")
            params.extend(tag_list)
    if search:
        pattern = f"%{search.lower()}%"
        where.append("(LOWER(COALESCE(headline, '')) LIKE %s OR LOWER(COALESCE(comment, '')) LIKE %s)")
        params.extend([pattern, pattern])


    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    order_col = "created_at" if sort == "created_at" else "overall_experience"
    order_sql = "ASC" if order == "asc" else "DESC"

    offset = decode_cursor(cursor)
    rows = run(
        f"""
        SELECT * FROM feedback_profile
        {where_sql}
        ORDER BY {order_col} {order_sql}, id {order_sql}
        LIMIT %s OFFSET %s
        """,
        tuple(params + [limit, offset]),
        fetch="all",
    )
    # next_cursor if there might be more (cheap check by fetching one more? keep simple: compute count of page)
    next_cursor = encode_cursor(offset + len(rows)) if len(rows) == limit else None
    items = [row_to_profile_out(r) for r in rows]
    query_items = query_items_from_request(request)
    prev_cursor = encode_cursor(max(offset - limit, 0)) if offset > 0 else None
    links = build_collection_links(
        request.url.path,
        query_items,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
    )
    return {"items": items, "next_cursor": next_cursor, "count": len(items), "links": links}

@app.get("/feedback/profile/stats", response_model=Dict[str, object])
def profile_feedback_stats(
    request: Request,
    reviewee_profile_id: UUID = Query(...),
    tags: Optional[str] = Query(default=None),
    since: Optional[datetime] = Query(default=None),
):
    where, params = ["reviewee_profile_id=%s"], [str(reviewee_profile_id)]
    if since: where.append("created_at >= %s"); params.append(since)
    if tags:
        tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
        if tag_list:
            where.append("JSON_OVERLAPS(tags, CAST(%s AS JSON))")
            params.append(str(tag_list).replace("'", '"'))
    where_sql = "WHERE " + " AND ".join(where)

    agg = run(
        f"""
        SELECT
          COUNT(*) AS total,
          AVG(overall_experience) AS avg_overall,
          SUM(overall_experience=1) AS d1,
          SUM(overall_experience=2) AS d2,
          SUM(overall_experience=3) AS d3,
          SUM(overall_experience=4) AS d4,
          SUM(overall_experience=5) AS d5,
          AVG(NULLIF(safety_feeling,0)) AS avg_safety,
          AVG(NULLIF(respectfulness,0)) AS avg_respect
        FROM feedback_profile
        {where_sql}
        """,
        tuple(params),
        fetch="one",
    )

    total = agg["total"] or 0
    query_items = query_items_from_request(request)
    stats_links = {
        "self": make_relative_url(request.url.path, query_items),
        "related_feedback": make_relative_url("/feedback/profile", query_items),
    }
    if total == 0:
        return {
            "reviewee_profile_id": reviewee_profile_id,
            "count_total": 0,
            "avg_overall_experience": None,
            "distribution_overall_experience": {str(k): 0 for k in range(1,6)},
            "facet_averages": {"safety_feeling": None, "respectfulness": None},
            "top_tags": [],
            "links": stats_links,
        }

    # Top tags via JSON_TABLE (MySQL 8+)
    top_tags = run(
        f"""
        SELECT jt.tag AS tag, COUNT(*) AS cnt
        FROM feedback_profile fp,
             JSON_TABLE(fp.tags, '$[*]' COLUMNS(tag VARCHAR(64) PATH '$')) jt
        {where_sql.replace('feedback_profile', 'fp')}
        GROUP BY jt.tag
        ORDER BY cnt DESC, jt.tag ASC
        LIMIT 10
        """,
        tuple(params),
        fetch="all",
    ) or []

    return {
        "reviewee_profile_id": reviewee_profile_id,
        "count_total": int(total),
        "avg_overall_experience": round(float(agg["avg_overall"]), 3) if agg["avg_overall"] is not None else None,
        "distribution_overall_experience": {
            "1": int(agg["d1"] or 0), "2": int(agg["d2"] or 0), "3": int(agg["d3"] or 0),
            "4": int(agg["d4"] or 0), "5": int(agg["d5"] or 0)
        },
        "facet_averages": {
            "safety_feeling": round(float(agg["avg_safety"]), 3) if agg["avg_safety"] is not None else None,
            "respectfulness": round(float(agg["avg_respect"]), 3) if agg["avg_respect"] is not None else None,
        },
        "top_tags": [{"tag": r["tag"], "count": int(r["cnt"])} for r in top_tags],
        "links": stats_links,
    }

# -------------------------------------------------------------------
# APP FEEDBACK (DB-backed)
# -------------------------------------------------------------------
@app.post("/feedback/app", response_model=AppFeedbackOut, status_code=status.HTTP_201_CREATED)
def create_app_feedback(payload: AppFeedbackCreate):
    now = datetime.utcnow()
    fid = str(uuid4())
    run(
        """
        INSERT INTO feedback_app
        (id, created_at, updated_at, author_profile_id, overall, usability, reliability, performance, support_experience,
         headline, comment, tags)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            fid, now, now,
            str(payload.author_profile_id) if payload.author_profile_id else None,
            payload.overall, payload.usability, payload.reliability, payload.performance, payload.support_experience,
            payload.headline, payload.comment,
            None if payload.tags is None else json.dumps(payload.tags),
        ),
    )
    row = run("SELECT * FROM feedback_app WHERE id=%s", (fid,), fetch="one")
    return row_to_app_out(row)

@app.get("/feedback/app/{id}", response_model=AppFeedbackOut)
def get_app_feedback(
    response: Response,
    id: UUID = Path(...),
    if_none_match: Optional[str] = Header(default=None),
):
    row = run("SELECT * FROM feedback_app WHERE id=%s", (str(id),), fetch="one")
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    etag = make_etag(row)
    tag_list = parse_etag_header(if_none_match)
    if if_none_match and (if_none_match.strip() == "*" or etag in tag_list):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

    response.headers["ETag"] = etag
    return row_to_app_out(row)

@app.patch("/feedback/app/{id}", response_model=AppFeedbackOut)
def update_app_feedback(
    response: Response,
    payload: AppFeedbackUpdate,
    id: UUID = Path(...),
    if_match: Optional[str] = Header(default=None),
):
    existing = run("SELECT * FROM feedback_app WHERE id=%s", (str(id),), fetch="one")
    if not existing:
        raise HTTPException(status_code=404, detail="Not found")

    current_etag = make_etag(existing)
    if if_match:
        tag_list = parse_etag_header(if_match)
        if if_match.strip() != "*" and current_etag not in tag_list:
            raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail="If-Match precondition failed")

    fields, params = [], []
    def setf(col, val): fields.append(f"{col}=%s"); params.append(val)

    data = payload.model_dump(exclude_unset=True)
    mapping = [
        ("author_profile_id", "author_profile_id"),
        ("overall", "overall"),
        ("usability", "usability"),
        ("reliability", "reliability"),
        ("performance", "performance"),
        ("support_experience", "support_experience"),
        ("headline", "headline"),
        ("comment", "comment"),
    ]
    for key, col in mapping:
        if key in data:
            v = data[key]
            if key == "author_profile_id":
                v = str(v) if v is not None else None
            setf(col, v)

    if "tags" in data:
        tags = data["tags"]
        setf("tags", None if tags is None else json.dumps(tags))

    if not fields:
        response.headers["ETag"] = current_etag
        return row_to_app_out(existing)

    params.append(str(id))
    sql = f"UPDATE feedback_app SET {', '.join(fields)}, updated_at=NOW(6) WHERE id=%s"
    run(sql, tuple(params))
    row = run("SELECT * FROM feedback_app WHERE id=%s", (str(id),), fetch="one")
    new_etag = make_etag(row)
    response.headers["ETag"] = new_etag
    return row_to_app_out(row)

@app.delete("/feedback/app/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_app_feedback(id: UUID = Path(...)):
    run("DELETE FROM feedback_app WHERE id=%s", (str(id),))
    return None

@app.get("/feedback/app", response_model=Dict[str, object])
def list_app_feedback(
    request: Request,
    author_profile_id: Optional[UUID] = Query(default=None),
    tags: Optional[str] = Query(default=None, description="Comma-separated list; OR semantics"),
    min_overall: Optional[int] = Query(default=None, ge=1, le=5),
    max_overall: Optional[int] = Query(default=None, ge=1, le=5),
    since: Optional[datetime] = Query(default=None),
    search: Optional[str] = Query(
        default=None,
        min_length=1,
        description="Case-insensitive search across headline/comment content",
    ),
    sort: str = Query(default="created_at", pattern="^(created_at|overall)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, description="Zero-based offset for pagination"),
    cursor: Optional[str] = Query(
        default=None,
        description="Base64 cursor (takes precedence over offset for backwards compatibility)",
    ),
):
    where, params = [], []
    if author_profile_id: where.append("author_profile_id=%s"); params.append(str(author_profile_id))
    if since: where.append("created_at >= %s"); params.append(since)
    if min_overall is not None: where.append("overall >= %s"); params.append(min_overall)
    if max_overall is not None: where.append("overall <= %s"); params.append(max_overall)
    if tags:
        tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
        if tag_list:
            where.append("JSON_OVERLAPS(tags, CAST(%s AS JSON))")
            params.append(str(tag_list).replace("'", '"'))
    if search:
        pattern = f"%{search.lower()}%"
        where.append("(LOWER(COALESCE(headline, '')) LIKE %s OR LOWER(COALESCE(comment, '')) LIKE %s)")
        params.extend([pattern, pattern])

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    order_col = "created_at" if sort == "created_at" else "overall"
    order_sql = "ASC" if order == "asc" else "DESC"

    effective_offset = decode_cursor(cursor) if cursor else offset
    rows = run(
        f"""
        SELECT * FROM feedback_app
        {where_sql}
        ORDER BY {order_col} {order_sql}, id {order_sql}
        LIMIT %s OFFSET %s
        """,
        tuple(params + [limit, effective_offset]),
        fetch="all",
    )
    total_row = run(
        f"SELECT COUNT(*) AS total FROM feedback_app {where_sql}",
        tuple(params),
        fetch="one",
    )
    total = total_row["total"] if total_row else 0
    next_cursor = encode_cursor(effective_offset + len(rows)) if len(rows) == limit else None
    items = [row_to_app_out(r) for r in rows]
    has_next = (effective_offset + len(rows)) < total
    previous_offset = max(effective_offset - limit, 0) if effective_offset > 0 else None
    next_offset = effective_offset + limit if has_next else None
    pagination = {
        "limit": limit,
        "offset": effective_offset,
        "count": len(items),
        "total": total,
        "has_next": has_next,
        "has_previous": effective_offset > 0,
        "next_offset": next_offset,
        "previous_offset": previous_offset,
        "next_cursor": next_cursor,
    }
    query_items = query_items_from_request(request)
    use_cursor = cursor is not None
    prev_cursor = (
        encode_cursor(max(effective_offset - limit, 0)) if use_cursor and effective_offset > 0 else None
    )
    links = build_collection_links(
        request.url.path,
        query_items,
        next_cursor=next_cursor if use_cursor else None,
        prev_cursor=prev_cursor,
        next_offset=None if use_cursor else next_offset,
        prev_offset=None if use_cursor else previous_offset,
    )
    return {
        "items": items,
        "next_cursor": next_cursor,
        "count": len(items),
        "pagination": pagination,
        "links": links,
    }

@app.get("/feedback/app/stats", response_model=Dict[str, object])
def app_feedback_stats(
    request: Request,
    tags: Optional[str] = Query(default=None),
    since: Optional[datetime] = Query(default=None),
):
    where, params = [], []
    if since: where.append("created_at >= %s"); params.append(since)
    if tags:
        tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
        if tag_list:
            where.append("JSON_OVERLAPS(tags, CAST(%s AS JSON))")
            params.append(str(tag_list).replace("'", '"'))
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    agg = run(
        f"""
        SELECT
          COUNT(*) AS total,
          AVG(overall) AS avg_overall,
          SUM(overall=1) AS d1,
          SUM(overall=2) AS d2,
          SUM(overall=3) AS d3,
          SUM(overall=4) AS d4,
          SUM(overall=5) AS d5,
          AVG(NULLIF(usability,0)) AS avg_usability,
          AVG(NULLIF(reliability,0)) AS avg_reliability,
          AVG(NULLIF(performance,0)) AS avg_performance,
          AVG(NULLIF(support_experience,0)) AS avg_support
        FROM feedback_app
        {where_sql}
        """,
        tuple(params),
        fetch="one",
    )
    total = agg["total"] or 0
    query_items = query_items_from_request(request)
    stats_links = {
        "self": make_relative_url(request.url.path, query_items),
        "related_feedback": make_relative_url("/feedback/app", query_items),
    }
    if total == 0:
        return {
            "count_total": 0,
            "avg_overall": None,
            "distribution_overall": {str(k): 0 for k in range(1,6)},
            "facet_averages": {"usability": None, "reliability": None, "performance": None, "support_experience": None},
            "top_tags": [],
            "links": stats_links,
        }

    top_tags = run(
        f"""
        SELECT jt.tag AS tag, COUNT(*) AS cnt
        FROM feedback_app fa,
             JSON_TABLE(fa.tags, '$[*]' COLUMNS(tag VARCHAR(64) PATH '$')) jt
        {where_sql.replace('feedback_app', 'fa')}
        GROUP BY jt.tag
        ORDER BY cnt DESC, jt.tag ASC
        LIMIT 10
        """,
        tuple(params),
        fetch="all",
    ) or []

    return {
        "count_total": int(total),
        "avg_overall": round(float(agg["avg_overall"]), 3) if agg["avg_overall"] is not None else None,
        "distribution_overall": {
            "1": int(agg["d1"] or 0), "2": int(agg["d2"] or 0), "3": int(agg["d3"] or 0),
            "4": int(agg["d4"] or 0), "5": int(agg["d5"] or 0)
        },
        "facet_averages": {
            "usability": round(float(agg["avg_usability"]), 3) if agg["avg_usability"] is not None else None,
            "reliability": round(float(agg["avg_reliability"]), 3) if agg["avg_reliability"] is not None else None,
            "performance": round(float(agg["avg_performance"]), 3) if agg["avg_performance"] is not None else None,
            "support_experience": round(float(agg["avg_support"]), 3) if agg["avg_support"] is not None else None,
        },
        "top_tags": [{"tag": r["tag"], "count": int(r["cnt"])} for r in top_tags],
        "links": stats_links,
    }

# -------------------------------------------------------------------
# Entrypoint
# -------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
