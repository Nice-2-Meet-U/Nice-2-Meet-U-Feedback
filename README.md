# Nice-2-Meet-U Feedback Microservice

API service for collecting and querying both profile-to-profile and app-level feedback within the Nice-2-Meet-U platform. Built with FastAPI, backed by MariaDB/MySQL JSON capabilities, and designed for easy consumption by other services.

## Getting Started
- **Install deps:** `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
- **Configure env:** copy `.env.example` (or create `.env`) and provide `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, and optional `FASTAPIPORT`.
- **Run locally:** `uvicorn main:app --reload --port ${FASTAPIPORT:-8000}`
- **Health check:** `curl http://localhost:8000/health`

On startup the service will auto-create the `feedback_profile` and `feedback_app` tables if they do not exist.

## Docker
- **Build:** `docker build -t feedback-service .`
- **Run (port 8001):** `docker run --env-file .env -e FASTAPIPORT=8001 -p 8001:8001 feedback-service`
- Override `FASTAPIPORT` to use a different container/host port (update both `-p` mapping and env var).
- **Compose (API + DB):** `FASTAPIPORT=8001 DB_PORT=3310 docker compose up --build`
- App connects to DB via service name `db` on port 3306 inside the network; host port is `DB_PORT` (default 3310). If you already have MySQL on the host, pick an unused `DB_PORT`.
- Compose waits for the DB healthcheck before starting the app (first start may take a few seconds).

## Data Model Highlights
- **feedback_profile**
  - Captures reviewer→reviewee meeting feedback.
  - `tags` persists as JSON (properly dumped on write, coerced to list on read).
  - Uniqueness guard: one feedback record per `(match_id, reviewer_profile_id)`.
- **feedback_app**
  - Stores overall impressions of the Nice-2-Meet-U application.
  - Also uses JSON-backed `tags`.

## Endpoints

### Health
`GET /health`  
`GET /health/{path_echo}`  
Returns uptime metadata, host IP, UTC timestamp, and echoes (query/path) for connectivity checks.

### Profile Feedback
- `POST /feedback/profile`
  - Body: `ProfileFeedbackCreate` (IDs, ratings, headline/comment, optional `tags: list[str]`).
  - Inserts a new record, returns `ProfileFeedbackOut` with generated `id` and timestamps.
- `GET /feedback/profile/{id}`
  - Path param: feedback UUID.
  - Returns the stored record or 404.
- `PATCH /feedback/profile/{id}`
  - Body: `ProfileFeedbackUpdate` (any subset of fields).
  - Updates mutable fields plus `updated_at`, returns the refreshed record.
- `DELETE /feedback/profile/{id}`
  - Removes the record (204 on success).
- `GET /feedback/profile`
  - Query params: filter by `reviewee_profile_id`, `reviewer_profile_id`, `match_id`, `tags` (comma-separated OR), `min_overall`, `max_overall`, `since`, `search` (headline/comment contains), plus cursor-based pagination (`limit`, `cursor`, `sort`, `order`).
  - Response: `{"items": [...ProfileFeedbackOut], "next_cursor": "...", "count": N}`.
- `GET /feedback/profile/stats`
  - Query params: `reviewee_profile_id` (required), optional `tags`, `since`.
  - Aggregates totals, averages, distributions, and top tags.

### App Feedback
- `POST /feedback/app`
  - Body: `AppFeedbackCreate` (overall/facet scores, headline/comment, optional `tags`).
  - Returns `AppFeedbackOut` with generated metadata.
- `GET /feedback/app/{id}`
  - Fetches a single record.
- `PATCH /feedback/app/{id}`
  - Partial updates via `AppFeedbackUpdate`.
- `DELETE /feedback/app/{id}`
  - Removes the entry (204).
- `GET /feedback/app`
  - Query params: `author_profile_id`, `tags`, ratings filters, `since`, `search`, sorting, and pagination controls (either cursor or explicit `offset` with `limit`).
  - Response mirrors the profile list shape but also includes a `pagination` object with `limit`, `offset`, `total`, and navigation helpers.
- `GET /feedback/app/stats`
  - Aggregates totals, rating distribution, facet averages, and tag counts (all optional filters except none required).

## Development Notes
- All UUIDs are stored as `CHAR(36)` strings; convert to/from `uuid.UUID` when instantiating Pydantic models.
- `tags` insert/update paths use `json.dumps` and `_coerce_tags` ensures outbound data is always `list[str]`.
- Collection pagination defaults to base64 cursoring for backwards compatibility, while the app feedback listing also supports explicit limit/offset metadata for page-based clients.
- Tests can be written with `pytest` (see `requirements.txt`).
