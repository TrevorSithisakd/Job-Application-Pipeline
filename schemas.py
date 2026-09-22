"""The contract for every LLM output.

Validation = parsing raw LLM JSON into one of these models. If it doesn't
parse, the output is rejected/retried. This is your first line of defence
against garbage rows and fabrication.

LEARN: pydantic v2 (BaseModel, Field constraints, Literal enums, Optional).
"""
from __future__ import annotations
from datetime import date
from typing import Literal, Optional
from pydantic import (BaseModel, Field, computed_field, field_validator,
                      model_validator)


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


class JobList(BaseModel):
    """The EXTRACT stage's output. A single alert email is often a DIGEST listing
    many jobs (SEEK recommendations, LinkedIn round-ups), so extraction returns a
    list, not one job. A non-posting email (status update, newsletter) yields []."""
    jobs: list[Job] = []


# NOTE on `track`: it was a Literal of four fixed values. Tracks are configurable
# now (JobPreferences.tracks), and a Literal cannot depend on runtime settings, so
# it is a plain str. The validation did not disappear — it moved to
# fitscore.fit_score(), which checks the value against the CONFIGURED track list
# and falls back to "none". A stale Literal here would have rejected a perfectly
# valid custom track as malformed LLM output.
#
# This is a comment rather than a docstring on purpose: these docstrings are sent
# to the model verbatim via model_json_schema(), and notes about our own refactor
# are noise in a scoring prompt.

class FitScore(BaseModel):
    """Produced by the FIT-SCORE stage. score is constrained 0-100."""
    score: int = Field(ge=0, le=100)
    rationale: str = Field(min_length=1)
    missing_keywords: list[str] = []
    track: str = "none"


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


# --- Settings (Setup pane) ---------------------------------------------------
# These are contracts too, just with a human on the other side instead of an LLM.
# Modelling them here rather than reading loose dicts out of JSON means a
# corrupted settings file or a bad slider value is rejected at the boundary with
# a field-level error, exactly like a malformed LLM response is.

DEFAULT_TRACKS = ["ml-engineer", "data-scientist", "data-analyst"]


class JobPreferences(BaseModel):
    """What an ideal role looks like to you. Replaces the hardcoded
    "Sydney/remote-AU" and the fixed track list that used to live inside the
    fit-score prompt (stages/fitscore.py RUBRIC)."""
    target_titles: list[str] = []
    locations: list[str] = []
    remote_policy: Literal["any", "remote", "hybrid", "onsite"] = "any"
    seniority: list[str] = []                    # e.g. ["graduate", "junior"]
    salary_floor: Optional[int] = Field(default=None, ge=0)
    salary_currency: str = "AUD"
    must_have_skills: list[str] = []
    nice_to_have_skills: list[str] = []
    dealbreakers: list[str] = []                 # e.g. "requires security clearance"
    tracks: list[str] = Field(default_factory=lambda: list(DEFAULT_TRACKS))

    @field_validator("tracks")
    @classmethod
    def _non_empty_tracks(cls, v: list[str]) -> list[str]:
        """An empty track list would make the model invent categories, so the
        score board would group jobs under labels that change every run."""
        cleaned = [t.strip() for t in v if t.strip()]
        return cleaned or list(DEFAULT_TRACKS)

    def all_tracks(self) -> list[str]:
        """Configured tracks plus the always-available "none" escape hatch — the
        model must be able to say "this fits no track I was given"."""
        return [*self.tracks, "none"]


class RubricDimension(BaseModel):
    """One weighted thing the fit score is judged on. `key` is stable (used for
    matching on save); `label` is what the model and the UI both read."""
    key: str
    label: str
    weight: int = Field(ge=0, le=100)


DEFAULT_DIMENSIONS = [
    {"key": "skills", "label": "Skills and tooling overlap", "weight": 35},
    {"key": "seniority", "label": "Seniority match", "weight": 20},
    {"key": "location", "label": "Location and remote policy", "weight": 20},
    {"key": "track", "label": "Track fit", "weight": 15},
    {"key": "salary", "label": "Salary against your floor", "weight": 10},
]


class ScoringRubric(BaseModel):
    """How the fit score is judged. Weights are rendered into the prompt as an
    explicit priority list; the bands are calibration anchors that keep a 70
    meaning roughly the same thing across roles (the calibration problem the
    fitscore module docstring calls out)."""
    dimensions: list[RubricDimension] = Field(
        default_factory=lambda: [RubricDimension(**d) for d in DEFAULT_DIMENSIONS],
        min_length=1,
    )
    strong_min: int = Field(default=75, ge=0, le=100)     # >= this: strong match
    possible_min: int = Field(default=50, ge=0, le=100)   # >= this: worth a look
    band_notes: str = ""                                  # what the bands mean, your words
    extra_criteria: str = ""                              # free-text escape hatch

    @model_validator(mode="after")
    def _bands_ordered(self) -> "ScoringRubric":
        if self.possible_min >= self.strong_min:
            raise ValueError(
                "possible_min must be below strong_min — otherwise the 'worth a "
                "look' band is empty and every job is either strong or rejected."
            )
        return self

    def active_dimensions(self) -> list[RubricDimension]:
        """Weight 0 means "ignore this", so it is dropped from the prompt rather
        than sent as a zero the model has to reason about."""
        return [d for d in self.dimensions if d.weight > 0]


class EmailSource(BaseModel):
    """One sender to scrape, as a bare domain ("seek.com.au") or a full address
    ("jobalerts-noreply@linkedin.com"). Gmail's from: operator matches both, and
    the original hardcoded _SENDERS list already mixed the two forms."""
    value: str = Field(min_length=3)
    label: str = ""                                       # display name from the inbox
    enabled: bool = True
    origin: Literal["default", "scan", "manual"] = "manual"

    @field_validator("value")
    @classmethod
    def _normalise(cls, v: str) -> str:
        """Lowercase and strip any display-name wrapper, so "SEEK <A@Seek.com.au>"
        and "a@seek.com.au" cannot both end up in the list as separate entries."""
        v = v.strip().lower()
        if "<" in v and ">" in v:
            v = v[v.index("<") + 1:v.index(">")].strip()
        return v.lstrip("@")


class AppSettings(BaseModel):
    """The whole settings document, persisted as data/settings.json. The API key
    is deliberately NOT here — it lives in .env (see settings.py)."""
    preferences: JobPreferences = Field(default_factory=JobPreferences)
    rubric: ScoringRubric = Field(default_factory=ScoringRubric)
    email_sources: list[EmailSource] = []
    # False until the user has actually been through sender setup. Distinguishes
    # "no sources chosen yet" (offer the scan) from "deliberately chose none".
    sources_configured: bool = False
    # False until the first-run wizard has been passed once. A latch, not a live
    # check: it stays True if something required breaks later (an expired Gmail
    # token), because that deserves a banner, not a locked-out board.
    setup_completed: bool = False

    def enabled_sources(self) -> list[str]:
        return [s.value for s in self.email_sources if s.enabled]


# --- Sender discovery (Setup: email sources) --------------------------------

class SenderStat(BaseModel):
    """One sender found by the inbox scan. Crosses the API boundary to the Setup
    pane, so it is a model rather than a loose dict."""
    domain: str
    address: str = ""
    display_name: str = ""
    count: int = 0
    sample_subjects: list[str] = []
    # "heuristic" = matched a known job-board pattern locally; "llm" = the batched
    # classifier said yes; "unknown" = neither, shown unticked behind a toggle.
    confidence: Literal["heuristic", "llm", "unknown"] = "unknown"
    already_added: bool = False


class SenderVerdict(BaseModel):
    """The classifier's judgement on one sender."""
    domain: str
    is_job_alert: bool


class SenderClassification(BaseModel):
    """LLM contract for the batched sender classification — one call for all the
    senders the local heuristic could not place."""
    verdicts: list[SenderVerdict] = []


# --- Fact bank extraction (Setup: upload a resume) --------------------------
# The LLM returns STRUCTURE; Python renders the markdown. Same split as the
# tailor stage: never let the model emit the final artefact's layout.

class FactItem(BaseModel):
    text: str = Field(min_length=1)
    # True when the source resume was vague about it. Surfaced in the markdown so
    # you can see what to verify — the grounding check treats this file as truth,
    # so an unmarked guess would silently license a fabricated resume claim.
    uncertain: bool = False


class FactSection(BaseModel):
    heading: str = Field(min_length=1)
    items: list[FactItem] = Field(min_length=1)


class FactBankDoc(BaseModel):
    """A resume parsed into the fact bank's shape, pending your review."""
    name: str = ""
    contact: str = ""
    sections: list[FactSection] = Field(min_length=1)
