"""
Gmail cleanup: finds old promotional / newsletter emails and moves them to Trash.

Assumes you already have Gmail API OAuth set up (the same credentials.json /
token.json your existing email agent uses). If you don't have those yet, see
setup notes at the bottom of this file.

Usage:
    python cleanup_emails.py                  # DRY RUN — lists what would be deleted, deletes nothing
    python cleanup_emails.py --execute         # actually moves matching emails to Trash
    python cleanup_emails.py --older-than 90   # only touch emails older than 90 days (default: 180)
"""

import argparse
import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def get_gmail_service():
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


def find_candidates(service, older_than_days):
    # category:promotions catches most newsletters/marketing; older_than filters by age.
    # You can widen this later, e.g. add 'OR category:updates' or 'OR label:CATEGORY_SOCIAL'.
    query = f"category:promotions older_than:{older_than_days}d"
    print(f"Searching with query: {query}\n")

    messages = []
    page_token = None
    while True:
        result = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token, maxResults=100)
            .execute()
        )
        messages.extend(result.get("messages", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return messages


def describe_message(service, msg_id):
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=msg_id, format="metadata", metadataHeaders=["Subject", "From", "Date"])
        .execute()
    )
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    return headers.get("From", "?"), headers.get("Subject", "(no subject)"), headers.get("Date", "?")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Actually trash the matched emails (default: dry run)")
    parser.add_argument("--older-than", type=int, default=180, help="Only match emails older than N days (default 180)")
    args = parser.parse_args()

    service = get_gmail_service()
    candidates = find_candidates(service, args.older_than)

    if not candidates:
        print("No matching emails found.")
        return

    print(f"Found {len(candidates)} matching emails.\n")
    print("Sample (first 15):")
    for m in candidates[:15]:
        sender, subject, date = describe_message(service, m["id"])
        print(f"  [{date}] {sender} — {subject}")

    if not args.execute:
        print(f"\nDRY RUN — nothing deleted. Re-run with --execute to move these {len(candidates)} emails to Trash.")
        return

    confirm = input(f"\nType 'yes' to move all {len(candidates)} emails to Trash: ")
    if confirm.strip().lower() != "yes":
        print("Cancelled — nothing deleted.")
        return

    for i, m in enumerate(candidates, 1):
        service.users().messages().trash(userId="me", id=m["id"]).execute()
        if i % 20 == 0:
            print(f"  Trashed {i}/{len(candidates)}...")

    print(f"\nDone. {len(candidates)} emails moved to Trash (recoverable for 30 days).")


if __name__ == "__main__":
    main()

# --- Setup notes (only needed if your existing agent doesn't already have these) ---
# pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
# 1. Google Cloud Console -> enable Gmail API -> create OAuth Client ID (Desktop app)
# 2. Download the JSON, save it as credentials.json next to this script
# 3. First run opens a browser to authorize; creates token.json for future runs
# 4. Scope is "gmail.modify" (needed to trash), not "gmail.readonly"