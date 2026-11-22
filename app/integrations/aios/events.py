"""Canonical AIOS ↔ ARVO event envelope (single definition shared by all sides)."""

from pydantic import BaseModel, Field


class AiosEvent(BaseModel):
    type: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_.-]+$")
    payload: dict = Field(default_factory=dict, max_length=100)
    business_trace_id: str | None = Field(default=None, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    event_version: str = Field(default="1", min_length=1, max_length=16, pattern=r"^[A-Za-z0-9._-]+$")
