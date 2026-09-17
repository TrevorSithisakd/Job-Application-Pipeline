"""STAGE 3 — FIT-SCORE. Job + your profile -> FitScore. NOT rag.

Three things kept separate:
  - RUBRIC  = scoring logic. Now BUILT from settings (weights, bands, your
              free-text criteria) rather than frozen in a string literal.
  - PROFILE = data about you (injected as context every call, from data/profile.md)
  - PREFS   = what an ideal role looks like (locations, salary floor, must-haves,
              dealbreakers). Used to be hardcoded as "Sydney/remote-AU".
  - RETRIEVAL = not used here; your profile fits in the prompt.

Everything is read per call, never at import. The Setup pane edits all of it at
runtime, and an import-time constant cannot change without a restart.

LEARN: instructions-vs-context distinction, rubric design, constrained outputs,
       calibration (does a 70 mean the same thing across roles?), configuration
       as data rather than code.
"""
from __future__ import annotations
import json

import settings
from paths import PROFILE_FILE
from schemas import FitScore, Job, JobPreferences, ScoringRubric
from llm import call_structured


def profile() -> str:
    """Your profile, read fresh on every call.

    Fails loudly. Scoring against an empty profile produces numbers that look
    fine and mean nothing, so a missing profile must stop the run, not default.
    Moved from an import-time constant to a call: the file is editable in the
    Setup pane now, and a cached copy would score against the version that
    happened to be on disk at boot.
    """
    if not PROFILE_FILE.exists():
        raise FileNotFoundError(
            f"No candidate profile at {PROFILE_FILE}. Fit scores would be "
            "meaningless without it."
        )
    return PROFILE_FILE.read_text(encoding="utf-8")


def _bullet_list(label: str, values: list[str]) -> str:
    """One rendered preference line, or nothing. An empty preference is omitted
    entirely rather than sent as "Locations: (none)" — a blank constraint reads
    to the model as a real one and skews the score."""
    cleaned = [v.strip() for v in values if v and v.strip()]
    return f"- {label}: {', '.join(cleaned)}\n" if cleaned else ""


def render_preferences(prefs: JobPreferences) -> str:
    """The IDEAL ROLE block. Everything here used to be the literal phrase
    "location (Sydney/remote-AU)" buried in the prompt."""
    out = ""
    out += _bullet_list("Target titles", prefs.target_titles)
    out += _bullet_list("Preferred locations", prefs.locations)
    if prefs.remote_policy != "any":
        out += f"- Work arrangement: prefers {prefs.remote_policy}\n"
    out += _bullet_list("Target seniority", prefs.seniority)
    if prefs.salary_floor:
        out += (f"- Salary floor: {prefs.salary_floor:,} {prefs.salary_currency} "
                f"(score below this down; do not reject outright when pay is unstated)\n")
    out += _bullet_list("Must-have skills", prefs.must_have_skills)
    out += _bullet_list("Nice-to-have skills", prefs.nice_to_have_skills)
    out += _bullet_list("Dealbreakers (cap the score low if present)", prefs.dealbreakers)
    return out or "- No specific preferences set; judge on general fit to the profile.\n"


def render_weights(rubric: ScoringRubric) -> str:
    """Weights as an explicit, normalised priority list.

    Normalised to percentages so the numbers mean something to the model: raw
    sliders that happen to sum to 260 read as arbitrary magnitudes, while
    "35% of the score" is a directly usable instruction.
    """
    dims = rubric.active_dimensions()
    total = sum(d.weight for d in dims)
    if not dims or total == 0:
        return "- Weigh all factors equally.\n"
    return "".join(
        f"- {d.label}: {round(d.weight * 100 / total)}% of the score\n" for d in dims
    )


def build_rubric(prefs: JobPreferences | None = None,
                 rubric: ScoringRubric | None = None) -> str:
    """Compose the scoring system prompt from settings.

    Kept as a pure function of its two arguments (loading them only when not
    given) so it is testable without touching the settings file, and so the
    Setup pane can render a live preview of the exact prompt the sliders produce.
    """
    if prefs is None or rubric is None:
        current = settings.load()
        prefs = prefs if prefs is not None else current.preferences
        rubric = rubric if rubric is not None else current.rubric

    tracks = " | ".join(prefs.all_tracks())
    bands = (
        f"- {rubric.strong_min}-100: strong match, worth applying to now.\n"
        f"- {rubric.possible_min}-{rubric.strong_min - 1}: possible, with real gaps.\n"
        f"- 0-{rubric.possible_min - 1}: weak match, not worth the effort.\n"
    )
    extra = (f"\nADDITIONAL CRITERIA (from the candidate, weigh these too):\n"
             f"{rubric.extra_criteria.strip()}\n" if rubric.extra_criteria.strip() else "")
    notes = (f"\nWHAT THE BANDS MEAN TO THIS CANDIDATE:\n{rubric.band_notes.strip()}\n"
             if rubric.band_notes.strip() else "")

    return f"""
Score 0-100 how well THIS candidate fits THIS role.

WEIGHT THE SCORE ROUGHLY LIKE THIS:
{render_weights(rubric)}
WHAT THIS CANDIDATE WANTS IN A ROLE:
{render_preferences(prefs)}
SCORE BANDS — use these consistently so a score means the same thing across roles:
{bands}{notes}{extra}
Pick track: {tracks}. Use "none" when the role fits no listed track.
List keywords in the JD that are missing from the candidate profile.

Reply with ONLY a JSON object conforming to this schema:
{json.dumps(FitScore.model_json_schema(), indent=2)}

Do not wrap it in prose or markdown fences.
"""


def fit_score(job: Job) -> FitScore:
    """Score one job against the current profile, preferences, and rubric."""
    current = settings.load()
    prefs = current.preferences
    system = build_rubric(prefs, current.rubric)
    user = f"CANDIDATE PROFILE:\n{profile()}\n\nJOB:\n{job.model_dump_json(indent=2)}"
    result = call_structured(system, user, schema=FitScore, tier="cheap")

    # Track validation moved here from the schema, which can no longer hold a
    # Literal now that tracks are configurable. Normalise case/whitespace, then
    # fall back to "none" rather than storing an invented category that would
    # fragment the board's grouping.
    valid = {t.lower(): t for t in prefs.all_tracks()}
    result.track = valid.get((result.track or "").strip().lower(), "none")
    return result
