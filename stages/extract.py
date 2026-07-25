"""STAGE 2 — EXTRACT. raw email -> list of validated Jobs.

Prompt construction, NOT retrieval. Input is the email itself. You build a
prompt = instructions + target schema + cleaned email, call at temperature 0,
and parse into a JobList (validation).

Alert emails are frequently DIGESTS: one SEEK "recommendations" or LinkedIn
round-up email lists a dozen jobs. Extracting only the first threw the other
eleven away, so extraction returns a LIST. Dedup happens at the DB by each job's
URL (a digest shares one email, but every job has its own posting link).

LEARN: prompt design for extraction, JSON/structured output, pydantic validation,
       one-to-many extraction, idempotency/dedup.
"""
from __future__ import annotations
import json
from schemas import Job, JobList
from llm import call_structured

# The model cannot match a schema it has never seen. Generating this from the
# pydantic model (rather than hand-writing the field list) means the prompt can
# never drift out of sync with what validation actually demands.
_SCHEMA = json.dumps(JobList.model_json_schema(), indent=2)

SYSTEM = f"""You extract EVERY job posting in a job-alert email into JSON.

Many of these emails are DIGESTS that list multiple jobs (e.g. "…+ 11 new jobs").
Extract ALL of them, not just the first. Each job in the email becomes one item.

Return ONLY a JSON object of the form {{"jobs": [ ... ]}}, where each item conforms
to this schema:
{_SCHEMA}

Field rules (apply to every job):
- Use these exact key names. Do not rename them and do not add extra keys.
- source: the alert's origin, as a lowercase slug — "seek-alert",
  "linkedin-alert", "indeed-alert", "greenhouse-alert". Infer it from the URLs
  or branding in the email. Use "unknown-alert" if genuinely unclear.
- title: the role name exactly as advertised.
- jd_text: the description/teaser text shown for THAT job, verbatim (its bullets,
  summary, salary line). Digests only carry a teaser — capture whatever is there.
- url: that job's OWN posting link (each row in a digest has its own link). This
  is important: it is the dedup key and the source for the full description later.
- deadline: ISO date (YYYY-MM-DD) or null — the CLOSING date, not the posting date.
- Every other field: use null when the email does not state it.
- Do not invent values. Null is always better than a guess.

If the email contains no job postings at all (an application status update, a
newsletter, a profile-view notice), return {{"jobs": []}}.
"""


def extract(email_body: str) -> list[Job]:
    """Every job posting in the email, validated. Empty list = not a posting email
    (the pipeline simply adds nothing, which is the correct outcome)."""
    # retries=1: with the schema in the prompt, a second failure means the email
    # isn't a job posting, and re-asking just spends tokens to fail again.
    result = call_structured(SYSTEM, email_body, schema=JobList, tier="cheap", retries=1)
    return result.jobs
