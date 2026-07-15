"""STAGE 1 — INGEST. Gmail job-alert emails -> list of (email_id, raw_body).

No scraping. Job-alert emails from Seek/LinkedIn/Indeed are structured, legal,
and free. This is the whole ingestion layer for v1.

LEARN: Gmail API OAuth flow, Gmail query syntax (from:, subject:, newer_than:),
       MIME/email parsing, stripping HTML to clean text.
"""
from __future__ import annotations


def fetch_job_emails(query: str = 'subject:(job OR role) newer_than:7d') -> list[tuple[str, str]]:
    """Return [(gmail_message_id, clean_text_body), ...].

    TODO:
      1. OAuth into Gmail (google-auth-oauthlib), build the service.
      2. users().messages().list(q=query) -> ids.
      3. For each id, get the message, decode the body, strip HTML/footers.
    """
    raise NotImplementedError
    