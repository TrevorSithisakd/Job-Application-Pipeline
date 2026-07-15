"""STAGE 1 — INGEST. Gmail job-alert emails -> list of (email_id, raw_body).

LEARN: Gmail API OAuth flow, Gmail query syntax (from:, subject:, newer_than:),
       MIME/email parsing, stripping HTML to clean text.
"""
from __future__ import annotations
import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

def fetch_job_emails(query: str = 'subject:(job OR role) newer_than:7d') -> list[tuple[str, str]]:
    """Return [(gmail_message_id, clean_text_body), ...].

    TODO:
      1. OAuth into Gmail (google-auth-oauthlib), build the service.
      2. users().messages().list(q=query) -> ids.
      3. For each id, get the message, decode the body, strip HTML/footers.
    """
    service = _gmail_service()
    resp = service.users().messages().list(userId="me", q=query).execute()
    results = []
    for msg in resp.get("message", []):
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

def extract_text():
  pass

if __name__ == "__main__":
  print(fetch_job_emails())

