"""STAGE 3 — FIT-SCORE. Job + your profile -> FitScore. NOT rag.

Three things kept separate:
  - RUBRIC  = scoring logic (fixed instructions, below)
  - PROFILE = data about you (injected as context every call, from data/profile.md)
  - RETRIEVAL = not used here; your profile fits in the prompt.

LEARN: instructions-vs-context distinction, rubric design, constrained outputs,
       calibration (does a 70 mean the same thing across roles?).
"""
from __future__ import annotations
from pathlib import Path
from schemas import Job, FitScore
from llm import call_structured

RUBRIC = """
Score 0-100 how well THIS candidate fits THIS role. Weigh:
- skills/tools overlap, seniority match, location (Sydney/remote-AU), track fit.
Pick track: ml-engineer | data-scientist | data-analyst | none.
List keywords in the JD that are missing from the candidate profile.
"""

PROFILE = Path("data/profile.md").read_text() if Path("data/profile.md").exists() else ""


def fit_score(job: Job) -> FitScore:
    user = f"CANDIDATE PROFILE:\n{PROFILE}\n\nJOB:\n{job.model_dump_json(indent=2)}"
    return call_structured(RUBRIC, user, schema=FitScore, tier="cheap")
