"""STAGE 1 — INGEST. Gmail job-alert emails -> list of (email_id, raw_body).

LEARN: Gmail API OAuth flow, Gmail query syntax (from:, subject:, newer_than:),
       MIME/email parsing, stripping HTML to clean text.
"""
from __future__ import annotations
import base64
import json
import re
import sys
from email.utils import parseaddr
from html import unescape

from collections import Counter
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from paths import CREDENTIALS_FILE, TOKEN_FILE

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _sender_query() -> str:
    """The `from:(...)` clause, built from the senders configured in Setup.

    This was a hardcoded module constant. It now comes from settings, which fall
    back to the same built-in list when nothing is configured — so an install
    that never opens the Setup pane ingests exactly what it did before.

    Imported lazily: settings imports schemas, and this module is imported by
    api.py during boot; keeping the import inside the function avoids adding
    another import-time dependency to the startup path.
    """
    import settings
    return " OR ".join(settings.sender_values())


def credentials_ready() -> bool:
    """True if a Gmail service can be built WITHOUT an interactive login — i.e.
    the token is valid or silently refreshable. The web app calls this before an
    ingest run, because a browser fetch can't complete the OAuth consent flow.
    """
    if not TOKEN_FILE.exists():
        return False
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    except Exception:
        return False
    if creds.valid:
        return True
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            return True
        except RefreshError:
            return False
    return False


# Gmail returns pages of ~100. A cap keeps a wide window from turning into
# thousands of per-message API calls; it is high enough that a normal alert
# volume never reaches it.
MAX_MESSAGES = 500


def fetch_job_emails(days: int = 7, query: str | None = None,
                     max_messages: int = MAX_MESSAGES) -> list[tuple[str, str]]:
    """Return [(gmail_message_id, clean_text_body), ...] for alerts in the last
    `days` days. `query` overrides the built default entirely.

    Senders come from the Setup pane (scan your inbox, or add them by hand).
    Note: noreply@s.seek.com.au also sends application-status emails - those flow
    through here by design and get rejected downstream (extract/fitscore).

    PAGINATES. This used to read only the first page of results — roughly 100
    messages — and silently drop the rest, which was easy to miss while the
    sender list was a fixed handful. Now that you can add senders from the UI,
    a wide window plus a few boards blows past one page routinely, so the run
    would quietly skip the oldest alerts in its own window.
    """
    if query is None:
        query = f"from:({_sender_query()}) newer_than:{days}d"
    service = _gmail_service()

    messages = []
    page_token = None
    while len(messages) < max_messages:
      resp = (service.users().messages()
              .list(userId="me", q=query, maxResults=100, pageToken=page_token)
              .execute())
      messages.extend(resp.get("messages", []))
      page_token = resp.get("nextPageToken")
      if not page_token:
        break
    messages = messages[:max_messages]

    results = []
    for msg in messages:
      msg_id = msg["id"]
      full = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
      body = extract_text(full["payload"])

      # Never hand a None/blank body downstream: the API rejects non-string
      # content outright, which reads as a confusing 400 rather than "no text".
      if not body or not body.strip():
        print(f"  [no body] {msg_id}")
        continue

      results.append((msg_id, body))
    return results

def _gmail_service():
  """Gets the service object for credentials for gmail API
  """
  creds = None
  # The file token.json stores the user's access and refresh tokens, and is
  # created automatically when the authorization flow completes for the first
  # time.
  if TOKEN_FILE.exists():
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
  # If there are no (valid) credentials available, let the user log in.
  if not creds or not creds.valid:
    # Try a silent refresh first. Google expires refresh tokens after ~7 days
    # for apps still in "testing", so a revoked token is expected, not
    # exceptional — catch it and fall through to a fresh login instead of
    # crashing (which is what forced the manual token.json delete).
    if creds and creds.expired and creds.refresh_token:
      try:
        creds.refresh(Request())
      except RefreshError:
        creds = None
    if not creds or not creds.valid:
      flow = InstalledAppFlow.from_client_secrets_file(
          str(CREDENTIALS_FILE), SCOPES
      )
      creds = flow.run_local_server(port=0)
    # Save the credentials for the next run
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
  # build the resource
  service = build("gmail", "v1", credentials=creds)
  return service

def extract_text(payload):
  """Best available body text, or None if the email carries none.

  Prefers text/plain across the WHOLE MIME tree before falling back to HTML —
  a plain part nested deeper than an HTML one still wins. Alerts that ship
  HTML only used to fall through here and return None, which the API then
  rejected as a malformed request.
  """
  plain = _find_part(payload, "text/plain")
  if plain:
    return _clean_text(plain)
  html_body = _find_part(payload, "text/html")
  if html_body:
    return _clean_text(_strip_html(html_body))
  return None

def _find_part(payload, mime):
  """Depth-first search of the MIME tree for the first part of `mime`."""
  if payload.get("mimeType") == mime:
    data = payload.get("body", {}).get("data")
    if data:
      # errors="replace" so one bad byte degrades a character, not the run.
      return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
  for part in payload.get("parts") or []:
    found = _find_part(part, mime)
    if found:
      return found
  return None

def _strip_html(raw):
  """Crude tag strip. Good enough for job alerts; not a general HTML parser."""
  raw = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)   # drop non-content
  raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", raw)  # blocks -> newlines
  raw = re.sub(r"<[^>]+>", "", raw)                       # remaining tags
  return unescape(raw)                                    # &amp; -> &

def _clean_text(text):
  text = text.split("This email was intended")[0]
  # NOTE: do NOT strip "?..." here. A previous `re.sub(r"\?\S+", "", text)`
  # deleted every URL query string, which is exactly where Indeed stores the
  # job id (?jk=...) — it collapsed real links to a dead /rc/clk/dl stub.
  text = text.replace("\r\n", "\n")          # normalize Windows line endings first
  text = re.sub(r"\n{3,}", "\n\n", text)     # collapse 3+ newlines to a paragraph break
  return text

# --- Sender discovery (Setup: email sources) --------------------------------
# Finding which senders to scrape used to mean running `python -m stages.ingest
# audit` and reading a wall of addresses. Same sweep, but it returns data now so
# the Setup pane can show it as a checklist.

DEFAULT_AUDIT_QUERY = "category:updates newer_than:90d"
MAX_SCAN_MESSAGES = 800

# Domains and subject phrases that are job alerts with no further thought needed.
# Cheap, offline, and deterministic — worth trying before spending an LLM call.
_JOB_DOMAIN_HINTS = (
    "linkedin", "seek.com", "indeed", "greenhouse", "lever.co", "workday",
    "glassdoor", "jora", "ziprecruiter", "jobs2web", "smartrecruiters",
    "myworkday", "careers", "recruit", "hired", "dice.com", "monster",
    "adzuna", "builtin", "wellfound", "angel.co", "talent",
)
_JOB_LOCALPART_HINTS = ("jobalert", "jobs-", "jobs@", "job-alert", "jobsalert",
                        "noreply-jobs", "alerts", "jobalerts")
_JOB_SUBJECT_HINTS = (
    "job alert", "new jobs", "jobs for you", "job recommendations", "now hiring",
    "new job", "jobs matching", "recommended for you", "apply now", "job digest",
    "vacanc", "position", "hiring",
)


def _parse_from(raw: str) -> tuple[str, str, str]:
  """"SEEK <noreply@s.seek.com.au>" -> (display, address, domain).

  Uses email.utils.parseaddr rather than a regex: From headers legitimately
  contain quoted commas and angle brackets inside the display name, which is
  exactly where hand-rolled splitting goes wrong.
  """
  display, address = parseaddr(raw or "")
  address = address.strip().lower()
  domain = address.split("@")[-1] if "@" in address else ""
  return display.strip().strip('"'), address, domain


def _looks_like_job_alert(domain: str, address: str, subjects: list[str]) -> bool:
  """Local heuristic. Deliberately generous on domains and conservative overall:
  a false positive costs one unticked checkbox, a false negative means you never
  see that sender in the list at all."""
  hay = f"{domain} {address}".lower()
  if any(h in hay for h in _JOB_DOMAIN_HINTS):
    return True
  if any(h in hay for h in _JOB_LOCALPART_HINTS):
    return True
  subject_blob = " ".join(subjects).lower()
  return any(h in subject_blob for h in _JOB_SUBJECT_HINTS)


def scan_senders(query: str = DEFAULT_AUDIT_QUERY,
                 max_messages: int = MAX_SCAN_MESSAGES,
                 progress: dict | None = None) -> list[dict]:
  """Sweep a broad window and return every sender found, most frequent first.

  format='metadata' fetches only the named headers — far cheaper than 'full'
  when bodies aren't needed. Subjects come back in the SAME call as the From
  header because they cost nothing extra there and are the strongest signal for
  classifying an ambiguous sender.

  Grouped by DOMAIN, not address: boards rotate local parts
  (jobs-listings@ / jobalerts-noreply@ / noreply@) but keep the domain, and
  Gmail's from: operator matches a bare domain fine.

  `progress`, if given, is the shared dict the web app polls — same contract as
  pipeline.run().
  """
  service = _gmail_service()
  counts: Counter[str] = Counter()
  info: dict[str, dict] = {}
  seen = 0
  page_token = None

  while seen < max_messages:
    resp = (
        service.users().messages()
        .list(userId="me", q=query, maxResults=100, pageToken=page_token)
        .execute()
    )
    batch = resp.get("messages", [])
    for msg in batch:
      if seen >= max_messages:
        break
      meta = (
          service.users().messages()
          .get(userId="me", id=msg["id"], format="metadata",
               metadataHeaders=["From", "Subject"])
          .execute()
      )
      headers = {h["name"]: h["value"] for h in meta["payload"].get("headers", [])}
      display, address, domain = _parse_from(headers.get("From", ""))
      seen += 1
      if not domain:
        continue
      counts[domain] += 1
      entry = info.setdefault(domain, {"address": address, "display_name": display,
                                       "subjects": []})
      subject = headers.get("Subject", "").strip()
      # Two samples is enough to classify and keeps the LLM payload small.
      if subject and len(entry["subjects"]) < 2 and subject not in entry["subjects"]:
        entry["subjects"].append(subject)
      if progress is not None and seen % 25 == 0:
        progress.update(scanned=seen, message=f"scanned {seen} emails…")
    page_token = resp.get("nextPageToken")
    if not page_token:
      break

  if progress is not None:
    progress.update(scanned=seen, message="classifying senders…")

  return [
      {"domain": domain,
       "address": info[domain]["address"],
       "display_name": info[domain]["display_name"],
       "count": n,
       "sample_subjects": info[domain]["subjects"]}
      for domain, n in counts.most_common()
  ]


def classify_senders(found: list[dict]) -> list[dict]:
  """Tag each scanned sender as heuristic / llm / unknown.

  Two passes so the LLM is a fallback, not the mechanism: the heuristic settles
  the obvious ones offline, and only what is left goes to the model — as ONE
  batched cheap-tier call, not one per sender. A scan of a busy inbox can turn up
  a hundred domains; per-sender calls would make this slow and needlessly costly.

  A failed or unavailable classification is not fatal: those senders simply stay
  "unknown" and appear unticked in the list, which is what they would have been
  without the call.
  """
  undecided = []
  for row in found:
    if _looks_like_job_alert(row["domain"], row["address"], row["sample_subjects"]):
      row["confidence"] = "heuristic"
    else:
      row["confidence"] = "unknown"
      undecided.append(row)

  if not undecided:
    return found

  try:
    verdicts = _llm_classify(undecided)
  except Exception as e:
    print(f"  [sender classify skipped] {type(e).__name__}: {e}")
    return found

  for row in undecided:
    if verdicts.get(row["domain"]):
      row["confidence"] = "llm"
  return found


_CLASSIFY_SYSTEM = """You identify which email senders send JOB ALERTS or job
postings — automated emails listing roles a candidate could apply for.

Say true for: job boards, applicant tracking systems, careers teams, recruiter
alert digests. Say false for everything else: newsletters, marketing, social
notifications, banking, receipts, and application STATUS updates that contain no
new postings.

You receive a JSON list of senders with sample subject lines. Return ONLY JSON:
{"verdicts": [{"domain": "<domain>", "is_job_alert": <bool>}]}
Give exactly one verdict per domain you were given."""


def _llm_classify(rows: list[dict]) -> dict[str, bool]:
  """One cheap-tier call for all undecided senders. Imported inside the function
  so this module stays importable (and `python -m stages.ingest audit` keeps
  working) on an install with no API key configured yet."""
  from llm import call_structured
  from schemas import SenderClassification

  payload = [{"domain": r["domain"], "sender": r["address"],
              "subjects": r["sample_subjects"]} for r in rows]
  result: SenderClassification = call_structured(
      _CLASSIFY_SYSTEM, "SENDERS:\n" + json.dumps(payload, indent=2),
      schema=SenderClassification, tier="cheap", temperature=0.0)
  return {v.domain.strip().lower(): v.is_job_alert for v in result.verdicts}


def discover_senders(query: str = DEFAULT_AUDIT_QUERY,
                     max_messages: int = MAX_SCAN_MESSAGES,
                     progress: dict | None = None) -> list[dict]:
  """Scan + classify + mark what is already configured. The Setup pane's one call."""
  import settings

  found = classify_senders(scan_senders(query, max_messages, progress=progress))
  configured = {s.value for s in settings.load().email_sources}
  for row in found:
    row["already_added"] = (row["domain"] in configured
                            or row["address"] in configured)
  return found


def audit_senders(query: str = DEFAULT_AUDIT_QUERY) -> None:
  """Print the scan, most frequent first — the original terminal workflow, kept
  working now that scan_senders() returns data instead of printing it."""
  for row in scan_senders(query):
    flag = "JOB?" if _looks_like_job_alert(
        row["domain"], row["address"], row["sample_subjects"]) else "    "
    print(f"{row['count']:4d}  {flag}  {row['domain']:40s}  {row['display_name']}")


if __name__ == "__main__":
  if "audit" in sys.argv:
    audit_senders()
  else:
    print(fetch_job_emails())

