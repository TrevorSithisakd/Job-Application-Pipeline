"""The contract for every LLM output.

Validation = parsing raw LLM JSON into one of these models. If it doesn't
parse, the output is rejected/retried. This is your first line of defence
against garbage rows and fabrication.

LEARN: pydantic v2 (BaseModel, Field constraints, Literal enums, Optional).
"""
from __future__ import annotations
from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, Field, computed_field


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


# --- TAILOR stage (stage 4) -------------------------------------------------
# These model the CONTENT of a resume, not its layout. Each field is typed so
# the deterministic renderer (Phase C) can format it distinctly, and each bullet
# is a discrete string so the grounding check (Phase B) can verify claims one at
# a time. The LLM selects and rewords from the fact bank into this shape; Python
# renders and enforces one page. Never let the model emit layout.

class ResumeProject(BaseModel):
    """One project block. `angle` is the JD-tailored suffix on the title, e.g.
    the same forecasting project becomes 'Ensemble Modelling & Monitoring' for
    one JD and 'Predictive Modelling & Multi-Source Features' for another."""
    title: str
    angle: str
    year: str
    stack: str                                   # italic sub-line (tech · github)
    url: str
    bullets: list[str] = Field(min_length=1, max_length=3)


class ExpItem(BaseModel):
    """One experience row (tutoring, retail). Distinct from ResumeProject:
    experience has no stack/url/angle and fewer bullets."""
    role: str
    org: str
    dates: str
    bullets: list[str] = Field(min_length=1, max_length=2)


class SkillLine(BaseModel):
    """One labelled skills line, e.g. label='Machine Learning',
    content='ridge, random forest, XGBoost, LightGBM'. A model rather than a
    (str, str) tuple so a validation error names the field that failed."""
    label: str
    content: str


class ResumeDraft(BaseModel):
    """Produced by the TAILOR stage — a structured, tailored, fact-grounded
    resume. Rendered deterministically to docx (Phase C); never approved until
    the grounding check (Phase B) and a human both pass."""
    tagline: str                                 # role focus under the name
    profile: str                                 # 3-4 lines, tailored to the JD
    projects: list[ResumeProject] = Field(min_length=1)
    skills: list[SkillLine] = Field(min_length=1)
    education: str
    experience: list[ExpItem] = []
    additional: Optional[str] = None             # one-line overflow absorber


# --- Grounding check (stage 4, safety layer) --------------------------------
# The second safety layer. A resume claim is only allowed to reach 'final' if it
# traces to the fact bank. GroundingReport is the raw per-index LLM verdict;
# GroundingResult is the resolved, stored form (claim text attached in Python).

class ClaimVerdict(BaseModel):
    """The checker's judgement on one numbered claim. index refers to the claim's
    position in the list sent to the model — results map back by index, never by
    re-matching (paraphrased) text."""
    index: int
    supported: bool
    evidence: str = ""                           # fact-bank phrase, "" if unsupported


class GroundingReport(BaseModel):
    """Raw LLM output of the grounding check: one verdict per claim index."""
    verdicts: list[ClaimVerdict]


class GroundedClaim(BaseModel):
    """A claim with its verdict resolved back to the claim text — the human-readable
    unit stored and shown in the dashboard."""
    claim: str
    supported: bool
    evidence: str = ""


class GroundingResult(BaseModel):
    """Resolved grounding outcome persisted alongside a resume draft. A draft with
    any unsupported claim cannot be auto-approved."""
    claims: list[GroundedClaim]

    @computed_field   # serialized into the stored JSON for quick human/dashboard reads
    @property
    def all_supported(self) -> bool:
        return all(c.supported for c in self.claims) if self.claims else True

    @property
    def flagged(self) -> list[GroundedClaim]:
        return [c for c in self.claims if not c.supported]
