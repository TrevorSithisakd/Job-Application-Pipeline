"""STAGE 4 — RESUME TAILOR (RAG + human approval). Job -> structured resume draft (pending).

v1 'RAG' = load your fact bank + the JD into context (no vector store). True
retrieval activates in Phase 2 when the corpus outgrows the window.

The LLM boundary is deliberately tight: the model SELECTS and REWORDS facts from
the fact bank into a structured `ResumeDraft`; Python renders and (later) enforces
one page. That split is what keeps the output both accurate and consistent.

Two safety layers before anything is 'final':
  1. GROUNDING CHECK  — every claim must trace to the fact bank. (Phase B, TODO)
  2. HUMAN APPROVAL   — you flip approved=1 in the DB / dashboard.

LEARN: RAG fundamentals (retrieval vs context-stuffing), structured output as the
       control surface, grounding/fact-checking to kill hallucination,
       human-in-the-loop, separating content generation from layout.
"""
from __future__ import annotations
import json
import sys
from dataclasses import dataclass

import db
from paths import FACT_BANK_FILE
from schemas import (Job, ResumeDraft, GroundingReport, GroundingResult,
                     GroundedClaim)
from llm import call_structured


def _load_fact_bank() -> str:
    """Fail loudly, mirroring fitscore._load_profile. Drafting a resume against
    an empty fact bank produces a blank or fabricated document that looks fine,
    so a missing fact bank must stop the run, not silently default to "".
    (The old relative-path read did exactly that when run from another cwd.)
    """
    if not FACT_BANK_FILE.exists():
        raise FileNotFoundError(
            f"No fact bank at {FACT_BANK_FILE}. A resume drafted against an "
            "empty fact bank would be blank or fabricated."
        )
    return FACT_BANK_FILE.read_text(encoding="utf-8")


FACT_BANK = _load_fact_bank()

# Generated from the model so the prompt can never drift from what validation
# demands (same discipline as the extract stage).
_SCHEMA = json.dumps(ResumeDraft.model_json_schema(), indent=2)

SYSTEM = f"""You are a resume writer. Draft a tailored, one-page resume for THIS job
using ONLY facts present in the fact bank.

Grounding (non-negotiable):
- Use ONLY facts in the fact bank. Never invent or embellish projects, metrics,
  tools, dates, or experience.
- You may reword, reorder, and re-emphasise to match the job. You may NOT add.
- Mirror the JD's exact tool/skill names ONLY when they are already true in the
  fact bank. Say "scikit-learn" if the JD does and the fact bank supports it;
  say "K-means/clustering" only because the Data Mining coursework supports it.
- Keep honest gaps honest. If the JD wants a tool the fact bank marks as a gap
  (Tableau, Power BI, Snowflake, SAS), never claim proficiency — surface the
  closest true equivalent (Streamlit/Matplotlib for BI, BigQuery for Snowflake).

Tailoring:
- Pick the 3-5 most JD-relevant projects, most relevant first. Set each project's
  `angle` to a short JD-tailored framing of the same true work.
- Order `skills` so the JD's top keywords appear in the first one or two lines.
- Curate the `education` line's coursework to the JD (surface Bayesian/econometrics
  for stats-heavy roles, Data Mining/clustering for analytics roles).
- Set `tagline` to mirror the JD's framing (e.g. "Statistical Modelling",
  "Deep Learning", "Data Science · Analytics · AI").
- `profile` is 3-4 tight lines. Each project has 1-3 bullets, each experience 1-2.

Prose style: plain and direct. No em dashes. No AI-tells — no rule-of-three
padding, no "in today's landscape", no explaining why something matters.

Return ONLY a JSON object conforming to this schema (no prose, no markdown fences):
{_SCHEMA}
"""


@dataclass
class TailorResult:
    """What the tailor stage produced for one job. Not an LLM contract (that's
    ResumeDraft) — an internal aggregate, so it lives here, not in schemas.py.
    Page-count fields get added in Phase D."""
    job_id: int
    resume_id: int
    version: int
    draft: ResumeDraft
    grounding: GroundingResult
    json_path: str
    md_path: str


def draft_structured(job: Job) -> ResumeDraft:
    """LLM -> validated ResumeDraft. Quality tier for prose; temp 0.3 for a
    little warmth (structure still holds via json_mode + validation + retry)."""
    user = f"FACT BANK:\n{FACT_BANK}\n\nJOB:\n{job.model_dump_json(indent=2)}"
    return call_structured(SYSTEM, user, schema=ResumeDraft,
                           tier="quality", temperature=0.3)


# --- Grounding check (safety layer 1) ---------------------------------------
# Fact-checking is the non-negotiable requirement, so this runs at the quality
# tier, temperature 0. It is a separate call from drafting: the drafter is
# incentivised to sound good, the checker only to be right.
GROUNDING_TIER = "quality"

GROUNDING_SYSTEM = """You are a strict resume fact-checker. You receive a FACT BANK
and a numbered list of CLAIMS taken from a drafted resume. Judge each claim ONLY
against the fact bank.

A claim is `supported` only if EVERY specific detail in it is backed by the fact
bank: the project, the metric, the tool/library, the dataset, the date, the scope,
and the action verb. Treat verb strength as a detail — "fine-tuned" is NOT supported
by a fact bank that only says a pretrained model was "used"; "built"/"designed" are
not supported if the fact bank only implies participation. When a claim adds any
specificity the fact bank does not explicitly contain, mark it unsupported.

Rewording, reordering, and re-emphasis are fine and stay supported as long as no
new fact is introduced.

Return ONLY JSON of the form:
{"verdicts": [{"index": <int>, "supported": <bool>, "evidence": "<fact-bank phrase>"}]}
Give exactly one verdict per claim index. `evidence` is the fact-bank phrase that
backs the claim, or "" when unsupported."""


def _collect_claims(d: ResumeDraft) -> list[str]:
    """Flatten the draft into the atomic, checkable claims, each prefixed with its
    source so the checker has context and a human can locate a flag. Tagline and
    profile are deliberately excluded — they are framing, not factual assertions."""
    claims: list[str] = []
    for p in d.projects:
        claims += [f"[Project: {p.title}] {b}" for b in p.bullets]
    for e in d.experience:
        claims += [f"[Experience: {e.role}, {e.org}] {b}" for b in e.bullets]
    claims += [f"[Skill] {s.label}: {s.content}" for s in d.skills]
    claims.append(f"[Education] {d.education}")
    return claims


def check_grounding(draft: ResumeDraft) -> GroundingResult:
    """Verify each claim in the draft against the fact bank. Results map back to
    claims by index; any claim the checker skips is conservatively flagged."""
    claims = _collect_claims(draft)
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(claims))
    user = f"FACT BANK:\n{FACT_BANK}\n\nCLAIMS:\n{numbered}"
    report: GroundingReport = call_structured(
        GROUNDING_SYSTEM, user, schema=GroundingReport,
        tier=GROUNDING_TIER, temperature=0.0)

    by_index = {v.index: v for v in report.verdicts}
    resolved: list[GroundedClaim] = []
    for i, claim in enumerate(claims):
        v = by_index.get(i)
        if v is None:                            # checker didn't rule on it -> flag
            resolved.append(GroundedClaim(
                claim=claim, supported=False, evidence="(no verdict returned)"))
        else:
            resolved.append(GroundedClaim(
                claim=claim, supported=v.supported, evidence=v.evidence))
    return GroundingResult(claims=resolved)


def _render_markdown(d: ResumeDraft) -> str:
    """A readable preview for the dashboard/CLI. NOT the deliverable — the
    one-page docx renderer is Phase C. Deterministic: no LLM here."""
    out = [f"_{d.tagline}_", "", d.profile, "", "## Projects"]
    for p in d.projects:
        out.append(f"### {p.title} — {p.angle} ({p.year})")
        out.append(f"_{p.stack} · {p.url}_")
        out += [f"- {b}" for b in p.bullets]
        out.append("")
    out.append("## Skills")
    out += [f"- **{s.label}:** {s.content}" for s in d.skills]
    out += ["", "## Education", d.education]
    if d.experience:
        out += ["", "## Experience"]
        for e in d.experience:
            out.append(f"### {e.role}, {e.org} ({e.dates})")
            out += [f"- {b}" for b in e.bullets]
    if d.additional:
        out += ["", f"**Additional:** {d.additional}"]
    return "\n".join(out)


def tailor_job(job_id: int) -> TailorResult:
    """Full stage: load job -> draft -> persist as pending. The single entry
    point the dashboard button (Phase E) will call.

    Opt-in per job (it costs the quality model), so this is NOT wired into the
    pipeline.run() loop.
    """
    db.init()                                    # ensure schema + grounding migration
    job = db.get_job(job_id)
    draft = draft_structured(job)
    grounding = check_grounding(draft)           # safety layer 1: fact-check every claim
    resume_id, version, json_path = db.save_resume(job_id, draft, grounding=grounding)
    md_path = json_path.with_suffix(".md")
    md_path.write_text(_render_markdown(draft), encoding="utf-8")
    return TailorResult(
        job_id=job_id, resume_id=resume_id, version=version,
        draft=draft, grounding=grounding,
        json_path=str(json_path), md_path=str(md_path),
    )


if __name__ == "__main__":
    # Run one real job end to end. From the project root:
    #   python -m stages.tailor            # top-fit job
    #   python -m stages.tailor <job_id>   # a specific job
    if len(sys.argv) > 1:
        _jid = int(sys.argv[1])
    else:
        _jobs = db.all_jobs()
        if not _jobs:
            raise SystemExit("No jobs in the DB. Run the pipeline first.")
        _jid = _jobs[0]["id"]
        print(f"No job id given; using top-fit job {_jid}: "
              f"{_jobs[0]['company']} - {_jobs[0]['title']}\n")
    _result = tailor_job(_jid)
    _g = _result.grounding
    _ok = len(_g.claims) - len(_g.flagged)
    print(f"Drafted resume v{_result.version} (pending) -> {_result.md_path}")
    print(f"Grounding: {_ok}/{len(_g.claims)} claims supported.")
    for _c in _g.flagged:
        print(f"  [FLAG] {_c.claim}")
    print()
    print(_render_markdown(_result.draft))
