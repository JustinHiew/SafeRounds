"""Request and response shapes for /v1. The conversation itself is validated by the clinic
(clinic.parse_conversation), not here, so each clinic's own record fields apply."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from safety_eval.schemas import Criterion, JudgeResult


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluateRequest(Request):
    conversation: dict
    known_names: list[str] = Field(default=[], max_length=50)    # names the de-identifier must remove
    prompt_version: str | None = None


class EvaluateResponse(BaseModel):
    result_id: int
    conversation_pk: int
    result: JudgeResult


class BatchRequest(Request):
    conversations: list[Any] = Field(min_length=1, max_length=1000)
    known_names: list[str] = Field(default=[], max_length=50)
    prompt_version: str | None = None


class Rejected(BaseModel):
    index: int
    conversation_id: str | None
    reasons: list[str]


class BatchCreated(BaseModel):
    batch_id: str
    status: str
    submitted: int
    rejected: list[Rejected]


class BatchItem(BaseModel):
    conversation_pk: int
    conversation_id: str
    result_id: int | None
    result: JudgeResult | None      # None while still queued


class BatchStatus(BaseModel):
    batch_id: str
    status: str
    prompt_version: str
    submitted: int
    rejected: int
    completed: int
    by_outcome: dict[str, int]
    flagged: int
    created_at: datetime
    finished_at: datetime | None
    offset: int
    limit: int
    results: list[BatchItem]


class FeedbackRequest(Request):
    result_id: int
    reviewer: str = Field(min_length=1, max_length=200)
    action: Literal["confirm", "overturn", "comment"]
    criterion: Criterion | None = None
    note: str = Field(default="", max_length=4000)


class FeedbackResponse(BaseModel):
    feedback_id: int


class DayStats(BaseModel):
    date: str
    evaluated: int = 0
    flagged: int = 0
    unsure: int = 0
    needs_review: int = 0
    criterion_fails: dict[str, int]


class StatsResponse(BaseModel):
    days: int
    daily: list[DayStats]


class ConversationOut(BaseModel):
    conversation_pk: int
    conversation_id: str
    batch_id: str | None
    conversation: dict
    created_at: datetime
