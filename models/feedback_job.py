from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class FeedbackAnalysisJobRequest(BaseModel):
    job_type: Literal["profile_stats", "app_stats"] = Field(
        ..., description="Type of feedback analysis job to run"
    )
    target_id: Optional[UUID] = Field(
        None,
        description="Profile identifier when job_type=profile_stats; ignored for app_stats",
    )
    tags: Optional[List[str]] = Field(
        default=None,
        description="Optional tag filters applied during aggregation",
    )
    since: Optional[datetime] = Field(
        default=None,
        description="Optional timestamp filter for recent feedback only",
    )

    @model_validator(mode="after")
    def _normalize(self):
        if self.job_type == "profile_stats" and self.target_id is None:
            raise ValueError("target_id is required for profile_stats jobs")
        if self.tags is not None:
            cleaned: List[str] = []
            for t in self.tags:
                if not t:
                    continue
                token = t.strip().lower()
                if token:
                    cleaned.append(token)
            self.tags = cleaned or None
        return self


class FeedbackAnalysisJobStatus(BaseModel):
    id: UUID
    job_type: Literal["profile_stats", "app_stats"]
    target_id: Optional[UUID]
    tags: Optional[List[str]]
    since: Optional[datetime]
    status: Literal["pending", "running", "succeeded", "failed"]
    result: Optional[Dict[str, object]] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    links: Dict[str, str]
