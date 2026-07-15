"""The contract for every LLM output.

Validation = parsing raw LLM JSON into one of these models. If it doesn't
parse, the output is rejected/retried. This is your first line of defence
against garbage rows and fabrication.

LEARN: pydantic v2 (BaseModel, Field constraints, Literal enums, Optional).
"""
from __future__ import annotations
from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, Field


class Job(BaseModel):
    """One role. Produced by the EXTRACT stage from a raw email."""
    source: str                      # e.g. "seek-alert", "linkedin-alert"
    company: str
    title: str
    jd_text: str
    location: Optional[str] = None
    salary: Optional[str] = None
    deadline: Optional[date] = None
    url: Optional[str] = None


class FitScore(BaseModel):
    """Produced by the FIT-SCORE stage. score is constrained 0-100."""
    score: int = Field(ge=0, le=100)
    rationale: str = Field(min_length=1)
    missing_keywords: list[str] = []
    track: Literal["ml-engineer", "data-scientist", "data-analyst", "none"]
