"""STAGE 1 — INGEST. Gmail job-alert emails -> list of (email_id, raw_body).

No scraping. Job-alert emails from Seek/LinkedIn/Indeed are structured, legal,
and free. This is the whole ingestion layer for v1.

LEARN: Gmail API OAuth flow, Gmail query syntax (from:, subject:, newer_than:),
       MIME/email parsing, stripping HTML to clean text.
"""
from __future__ import annotations

import base64
import os.path
from html.parser import HTMLParser
from io import StringIO

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Read-only is all we need: we never send or modify mail.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def fetch_job_emails(query: str = "subject:(job OR role) newer_than:7d") -> list[tuple[str, str]]:
    """Return [(gmail_message_id, clean_text_body), ...].

    1. OAuth into Gmail (google-auth-oauthlib), build the service.
    2. users().messages().list(q=query) -> ids.
    3. For each id, get the message, decode the body, strip HTML/footers.
    """
    service = _gmail_service()

    # list() is paginated and returns only ids/threadIds — 100 per page by default.
    ids: list[str] = []
    page_token: str | None = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token)
            .execute()
        )
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    results: list[tuple[str, str]] = []
    for msg_id in ids:
        # format="full" gives the MIME tree with base64url-encoded body parts.
        msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        body = _extract_body(msg["payload"])
        if body.strip():
            results.append((msg_id, body))
    return results


def _gmail_service():
    """Build an authenticated Gmail client (same OAuth flow as quickstart.py).

    token.json caches the user's tokens after the first browser login, so the
    flow only opens a browser once (or when the token can't be refreshed).
    """
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def _extract_body(payload: dict) -> str:
    """Walk the MIME tree and return clean text.

    Prefer text/plain; fall back to stripping text/html. Multipart emails nest
    parts (payload["parts"]), so we recurse. Bodies are base64url-encoded.
    """
    mime = payload.get("mimeType", "")

    if mime == "text/plain":
        return _normalize(_decode(payload["body"]))
    if mime == "text/html":
        return _normalize(_strip_html(_decode(payload["body"])))

    # multipart/* — recurse into children, preferring the first non-empty result.
    if payload.get("parts"):
        plain, html = "", ""
        for part in payload["parts"]:
            text = _extract_body(part)
            if not text:
                continue
            if part.get("mimeType") == "text/plain" and not plain:
                plain = text
            elif not html:
                html = text
        return plain or html
    return ""


def _decode(body: dict) -> str:
    """Decode a Gmail body part's base64url `data` field to text."""
    data = body.get("data")
    if not data:
        return ""
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")


def _normalize(text: str) -> str:
    """Collapse blank runs so downstream prompts aren't padded with whitespace."""
    lines = [line.strip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):  # keep single blank lines as paragraph breaks
            out.append(line)
    return "\n".join(out).strip()


class _TextExtractor(HTMLParser):
    """Minimal stdlib HTML -> text. Avoids adding a BeautifulSoup dependency."""

    def __init__(self) -> None:
        super().__init__()
        self._buf = StringIO()
        self._skip = False  # inside <script>/<style>

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("br", "p", "div", "tr", "li"):
            self._buf.write("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._buf.write(data)

    def text(self) -> str:
        return self._buf.getvalue()


def _strip_html(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


if __name__ == "__main__":
    # Quick manual test: prints how many alerts matched and a preview of the first.
    emails = fetch_job_emails()
    print(f"Fetched {len(emails)} job emails.")
    if emails:
        eid, body = emails[0]
        print(f"\n--- {eid} ---\n{body[:500]}")
