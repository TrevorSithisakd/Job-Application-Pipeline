"""STAGE 1 — INGEST. Gmail job-alert emails -> list of (email_id, raw_body).

LEARN: Gmail API OAuth flow, Gmail query syntax (from:, subject:, newer_than:),
       MIME/email parsing, stripping HTML to clean text.
"""
from __future__ import annotations
import os.path
import base64
import json
import re
import sys

from collections import Counter
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

def fetch_job_emails(query: str = (
    'from:(jobalerts-noreply@linkedin.com OR jobs-noreply@linkedin.com '
    'OR jobs-listings@linkedin.com OR notifications@us.greenhouse-jobs.com '
    'OR seek.com.au OR indeed.com OR jobs2web.com) newer_than:7d'
)) -> list[tuple[str, str]]:
    """Return [(gmail_message_id, clean_text_body), ...].

    Senders found via audit_senders(); re-run it periodically to catch new ones.
    Note: noreply@s.seek.com.au also sends application-status emails - those flow
    through here by design and get rejected downstream (extract/fitscore).
    """
    service = _gmail_service()
    resp = service.users().messages().list(userId="me", q=query).execute()
    results = []
    for msg in resp.get("messages", []):
      msg_id = msg["id"]
      full = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
      body = extract_text(full["payload"])

      results.append((msg_id, body))
    return results  

def _gmail_service():
  """Gets the service object for credentials for gmail API
  """
  creds = None
  # The file token.json stores the user's access and refresh tokens, and is
  # created automatically when the authorization flow completes for the first
  # time.
  if os.path.exists("token.json"):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
  # If there are no (valid) credentials available, let the user log in.
  if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
      creds.refresh(Request())
    else:
      flow = InstalledAppFlow.from_client_secrets_file(
          "credentials.json", SCOPES
      )
      creds = flow.run_local_server(port=0)
    # Save the credentials for the next run
    with open("token.json", "w") as token:
      token.write(creds.to_json())
  # build the resource
  service = build("gmail", "v1", credentials=creds)
  return service

def extract_text(payload):
  mime = payload.get("mimeType")
  if mime == "text/plain":
    data = payload["body"]["data"]
    return _clean_text(base64.urlsafe_b64decode(data).decode("utf-8"))
  if payload.get("parts"):
    for part in payload["parts"]:
      text = extract_text(part)
      if text:
        return text

def _clean_text(text):
  text = text.split("This email was intended")[0]
  text = re.sub(r"\?\S+", "", text)    # drop '?' and everything non-space after it
  text = text.replace("\r\n", "\n")          # normalize Windows line endings first
  text = re.sub(r"\n{3,}", "\n\n", text)     # collapse 3+ newlines to a paragraph break
  return text

def audit_senders(query: str = "category:updates newer_than:90d") -> None:
  """Recall audit: list every sender in a BROAD window, most frequent first.

  Eyeball the output for job-related senders missing from fetch_job_emails'
  default query. format='metadata' fetches only the named headers - much
  cheaper than format='full' when bodies aren't needed. Re-run every month
  or two, and after signing up to any new job board.
  """
  service = _gmail_service()
  senders: Counter[str] = Counter()
  page_token = None
  while True:  # unlike fetch, an audit should sweep ALL pages of results
    resp = (
        service.users().messages()
        .list(userId="me", q=query, maxResults=500, pageToken=page_token)
        .execute()
    )
    for msg in resp.get("messages", []):
      meta = (
          service.users().messages()
          .get(userId="me", id=msg["id"], format="metadata", metadataHeaders=["From"])
          .execute()
      )
      headers = {h["name"]: h["value"] for h in meta["payload"]["headers"]}
      senders[headers.get("From", "?")] += 1
    page_token = resp.get("nextPageToken")
    if not page_token:
      break
  for sender, n in senders.most_common():
    print(f"{n:4d}  {sender}")

if __name__ == "__main__":
  if "audit" in sys.argv:
    audit_senders()
  else:
    print(fetch_job_emails())

