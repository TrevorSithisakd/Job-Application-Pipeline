"""STAGE 2 — EXTRACT. raw email -> validated Job.

Prompt construction, NOT retrieval. Input is the email itself. You build a
prompt = instructions + target schema + cleaned email, call at temperature 0,
and parse into the Job model (validation). Dedup happens at the DB via email_id.

LEARN: prompt design for extraction, JSON/structured output, pydantic validation,
       idempotency/dedup.
"""
from __future__ import annotations
from schemas import Job
from llm import call_structured

SYSTEM = (
    "You extract a single job posting into JSON matching the schema. "
    "Use null for anything not present. Do not invent fields."
)


def extract(email_body: str) -> Job:
    return call_structured(SYSTEM, email_body, schema=Job, tier="cheap")
