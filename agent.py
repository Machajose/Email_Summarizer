"""
A minimal email-checking agent — no LangGraph, no ADK, just Python.

THE CORE IDEA (the "agent loop"):
    1. We tell the LLM what tools (Python functions) it's allowed to call.
    2. We send it a task in plain English.
    3. The LLM responds with either a final answer, OR a request to call one
       of our functions (with arguments it picked itself).
    4. We actually run that function in Python, and send the result back.
    5. Repeat until the LLM has enough info to give a final answer.

That's it. Everything below is just plumbing to support that loop.
This version uses Groq (free, no billing required) instead of Gemini.
"""

import os
import json
import base64
import requests
from dotenv import load_dotenv

load_dotenv()  # reads variables from a local .env file, if present

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("Set the GROQ_API_KEY environment variable first (see SETUP.md)")

GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Read-only Gmail access — the agent cannot send, delete, or modify anything.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


# ---------------------------------------------------------------------------
# GMAIL SETUP — this part has nothing to do with the LLM.
# It just gives us two plain Python functions the agent will be allowed to call.
# ---------------------------------------------------------------------------
def get_gmail_service():
    """Authenticate with Gmail (opens a browser the first time), return an API client."""
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


_gmail = get_gmail_service()


def list_unread_emails(max_results: int = 5) -> list[dict]:
    """Return the id, sender, and subject of the most recent unread emails."""
    results = _gmail.users().messages().list(
        userId="me", labelIds=["UNREAD"], maxResults=max_results
    ).execute()
    messages = results.get("messages", [])

    summaries = []
    for msg in messages:
        detail = _gmail.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject"]
        ).execute()
        headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
        summaries.append({
            "id": msg["id"],
            "from": headers.get("From", "(unknown sender)"),
            "subject": headers.get("Subject", "(no subject)"),
        })
    return summaries


def get_email_body(email_id: str) -> str:
    """Return the plain-text body of a specific email, given its id."""
    detail = _gmail.users().messages().get(userId="me", id=email_id, format="full").execute()
    payload = detail["payload"]

    def extract_text(part):
        if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
        for sub in part.get("parts", []):
            text = extract_text(sub)
            if text:
                return text
        return ""

    body = extract_text(payload)
    return body[:2000] if body else detail.get("snippet", "")


# A lookup so we can actually call the function Gemini asks for, by name.
AVAILABLE_FUNCTIONS = {
    "list_unread_emails": list_unread_emails,
    "get_email_body": get_email_body,
}

# This is the schema we hand to the LLM so it knows these functions exist,
# what they do, and what arguments they take. The LLM never runs the code
# itself — it just tells us "please call list_unread_emails(max_results=5)"
# and we do it. This is OpenAI's tool-schema format, which Groq also uses.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_unread_emails",
            "description": "List unread emails in the inbox (id, sender, subject).",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer", "description": "How many emails to fetch"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_body",
            "description": "Get the full text body of one specific email by its id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_id": {"type": "string", "description": "The Gmail message id"}
                },
                "required": ["email_id"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# THE AGENT LOOP
# ---------------------------------------------------------------------------
def call_groq(messages: list[dict]) -> dict:
    """Send the conversation so far to Groq, get its next move back."""
    response = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={"model": GROQ_MODEL, "messages": messages, "tools": TOOLS},
    )
    response.raise_for_status()
    return response.json()


def run_agent(task: str, max_steps: int = 6) -> str:
    messages = [{"role": "user", "content": task}]

    for step in range(max_steps):
        result = call_groq(messages)
        message = result["choices"][0]["message"]

        # Keep the model's own turn in the conversation history.
        messages.append(message)

        tool_calls = message.get("tool_calls")

        if not tool_calls:
            # No tool call this turn -> the model gave us its final answer.
            return message.get("content", "")

        # The model asked to call one or more functions. Actually run them.
        for call in tool_calls:
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"] or "{}")
            print(f"  [agent] calling {name}({args})")

            func = AVAILABLE_FUNCTIONS[name]
            output = func(**args)

            # Feed each function's result back so the model can continue,
            # tagged with the same tool_call id so it knows which call this answers.
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(output),
            })

    return "Stopped after too many steps without a final answer."


if __name__ == "__main__":
    task = (
        "Check my unread emails and give me a short summary of each one: "
        "who it's from, the subject, and one sentence on what it's about. "
        "If a subject line isn't clear enough on its own, look at the email body."
    )
    print("Running agent...\n")
    answer = run_agent(task)
    print("\n--- SUMMARY ---")
    print(answer)