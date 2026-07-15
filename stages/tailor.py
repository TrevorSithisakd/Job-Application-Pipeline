"""STAGE 4 — RESUME TAILOR (RAG + human approval). Job -> resume draft (pending).

v1 'RAG' = load your fact bank + past resume into context (no vector store).
True retrieval activates in Phase 2 when the corpus outgrows the window.

Two safety layers before anything is 'final':
  1. GROUNDING CHECK  — every claim in the draft must trace to the fact bank.
  2. HUMAN APPROVAL    — you flip approved=1 in the DB / dashboard.

LEARN: RAG fundamentals (retrieval vs context-stuffing), embeddings + cosine
       similarity (Phase 2), grounding/fact-checking to kill hallucination,
       human-in-the-loop, templating a resume.
"""
from __future__ import annotations
from pathlib import Path
from schemas import Job
from llm import call_llm

FACT_BANK = Path("data/fact_bank.md").read_text() if Path("data/fact_bank.md").exists() else ""

SYSTEM = (
    "Draft a tailored resume for this job using ONLY facts in the fact bank. "
    "Never invent experience, metrics, or dates. Emphasise what matches the JD."
)


def draft_resume(job: Job) -> str:
    user = f"FACT BANK:\n{FACT_BANK}\n\nJOB:\n{job.jd_text}"
    draft = call_llm(SYSTEM, user, tier="quality", temperature=0.3)
    # TODO: grounding check — verify each claim appears in FACT_BANK; strip/flag if not.
    return draft
