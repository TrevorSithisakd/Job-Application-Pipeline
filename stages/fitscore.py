"""STAGE 3 — FIT-SCORE. Job + your profile -> FitScore. NOT rag.

Three things kept separate:
  - RUBRIC  = scoring logic (fixed instructions, below)
  - PROFILE = data about you (injected as context every call, from data/profile.md)
  - RETRIEVAL = not used here; your profile fits in the prompt.

LEARN: instructions-vs-context distinction, rubric design, constrained outputs,
       calibration (does a 70 mean the same thing across roles?).
"""
from __future__ import annotations
import json
from paths import PROFILE_FILE
from schemas import Job, FitScore
from llm import call_structured

RUBRIC = f"""
Score 0-100 how well THIS candidate fits THIS role. Weigh:
- skills/tools overlap, seniority match, location (Sydney/remote-AU), track fit.
Pick track: ml-engineer | data-scientist | data-analyst | none.
List keywords in the JD that are missing from the candidate profile.

Reply with ONLY a JSON object conforming to this schema:
{json.dumps(FitScore.model_json_schema(), indent=2)}

Do not wrap it in prose or markdown fences.
"""

def _load_profile() -> str:
    """Fail loudly. Scoring against an empty profile produces numbers that look
    fine and mean nothing, so a missing profile must stop the run, not default.
    """
    if not PROFILE_FILE.exists():
        raise FileNotFoundError(
            f"No candidate profile at {PROFILE_FILE}. Fit scores would be "
            "meaningless without it."
        )
    return PROFILE_FILE.read_text(encoding="utf-8")


PROFILE = _load_profile()


def fit_score(job: Job) -> FitScore:
    user = f"CANDIDATE PROFILE:\n{PROFILE}\n\nJOB:\n{job.model_dump_json(indent=2)}"
    return call_structured(RUBRIC, user, schema=FitScore, tier="cheap")
