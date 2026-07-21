"""STAGE 2 — EXTRACT. raw email -> validated Job.

Prompt construction, NOT retrieval. Input is the email itself. You build a
prompt = instructions + target schema + cleaned email, call at temperature 0,
and parse into the Job model (validation). Dedup happens at the DB via email_id.

LEARN: prompt design for extraction, JSON/structured output, pydantic validation,
       idempotency/dedup.
"""
from __future__ import annotations
import json
from schemas import Job
from llm import call_structured

# The model cannot match a schema it has never seen. Generating this from the
# pydantic model (rather than hand-writing the field list) means the prompt can
# never drift out of sync with what validation actually demands.
_SCHEMA = json.dumps(Job.model_json_schema(), indent=2)

SYSTEM = f"""You extract a single job posting from a job-alert email into JSON.

Return ONLY a JSON object conforming to this schema:
{_SCHEMA}

Field rules:
- Use these exact key names. Do not rename them and do not add extra keys.
- source: the alert's origin, as a lowercase slug — "seek-alert",
  "linkedin-alert", "indeed-alert", "greenhouse-alert". Infer it from the URLs
  or branding in the email. Use "unknown-alert" if genuinely unclear.
- title: the role name exactly as advertised.
- jd_text: the description/requirements text, verbatim from the email. If the
  email only teases the role, use whatever descriptive text is present.
- deadline: ISO date (YYYY-MM-DD) or null. This is the CLOSING date, not the
  posting date — if only a posting date is given, use null.
- Every other field: use null when the email does not state it.
- Do not invent values. Null is always better than a guess.

If the email is not a job posting at all (an application status update, a
newsletter, a profile-view notice), omit the required fields. Failing
validation is the correct outcome — the pipeline skips such emails by design.
"""


def extract(email_body: str) -> Job:
    # retries=1, not the default 2: now that the schema is in the prompt, a
    # second failure means the email isn't a job posting, and re-asking just
    # spends tokens to fail again.
    return call_structured(SYSTEM, email_body, schema=Job, tier="cheap", retries=1)
