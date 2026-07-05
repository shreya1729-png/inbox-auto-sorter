"""
Email Auto-Responder + Categorizer -- Web version
---------------------------------------------------
A minimal Flask app: visitor clicks "Connect Gmail", logs in via Google,
we process their inbox (classify + label + draft replies), then show
a simple results page. No database, no persistent storage of credentials
beyond the current session.
"""

import os
import json
import base64
from email.mime.text import MIMEText

from flask import Flask, redirect, request, session, url_for, render_template_string
from werkzeug.middleware.proxy_fix import ProxyFix
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from openai import OpenAI

# ---------- CONFIG ----------
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
CATEGORIES = ["Urgent", "Sales Lead", "Support", "Notification", "Spam", "Other"]
LABEL_PREFIX = "AI/"
MAX_EMAILS_TO_PROCESS = 20
OPENAI_MODEL = "gpt-4o-mini"

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.environ["FLASK_SECRET_KEY"]

# Google OAuth client config comes from an environment variable (the full
# JSON contents of your "Web application" OAuth client, as one string)
CLIENT_CONFIG = json.loads(os.environ["GOOGLE_CLIENT_CONFIG"])

# Allow OAuth over plain http during local testing only.
if os.environ.get("FLASK_ENV") == "development":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

client = OpenAI()  # reads OPENAI_API_KEY from environment


# ---------- HTML ----------
HOME_PAGE = """
<!doctype html>
<html>
<head>
  <title>Inbox Auto-Sorter</title>
  <style>
    body { font-family: -apple-system, Arial, sans-serif; max-width: 480px; margin: 80px auto; text-align: center; color: #222; }
    h1 { font-size: 24px; }
    p { color: #555; }
    a.button { display: inline-block; margin-top: 24px; padding: 12px 28px; background: #1a73e8; color: white;
               text-decoration: none; border-radius: 6px; font-weight: 600; }
  </style>
</head>
<body>
  <h1>Sort your inbox automatically</h1>
  <p>Connect your Gmail and we'll label your recent emails (Urgent, Sales Lead, Support, Spam)
     and draft replies for you to review. Nothing is sent without your approval.</p>
  <a class="button" href="{{ auth_url }}">Connect Gmail</a>
</body>
</html>
"""

RESULT_PAGE = """
<!doctype html>
<html>
<head>
  <title>Done</title>
  <style>
    body { font-family: -apple-system, Arial, sans-serif; max-width: 600px; margin: 60px auto; color: #222; }
    h1 { font-size: 22px; }
    .item { border-bottom: 1px solid #eee; padding: 14px 0; }
    .cat { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px;
           font-weight: 600; background: #eef2ff; color: #3730a3; }
  </style>
</head>
<body>
  <h1>Done! Processed {{ count }} email(s).</h1>
  <p>Check your Gmail — new labels (AI/...) have been applied, and draft replies are waiting in your Drafts folder for review.</p>
  {% for r in results %}
    <div class="item">
      <div><b>{{ r.subject }}</b> &mdash; <span class="cat">{{ r.category }}</span></div>
      <div style="color:#666; font-size: 14px;">{{ r.reason }}</div>
    </div>
  {% endfor %}
</body>
</html>
"""


# ---------- GMAIL HELPERS ----------
def get_or_create_label(service, label_name):
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"] == label_name:
            return label["id"]
    new_label = service.users().labels().create(
        userId="me",
        body={"name": label_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
    ).execute()
    return new_label["id"]


def get_email_text(payload):
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
            if "parts" in part:
                text = get_email_text(part)
                if text:
                    return text
    elif "body" in payload and "data" in payload["body"]:
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
    return ""


def get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def classify_and_draft(sender, subject, body):
    prompt = f"""You are triaging a business inbox. Categorize the email below into exactly one of:
{CATEGORIES}

Category definitions:
- "Urgent": a real person needs a time-sensitive response (angry customer, deadline, problem to fix).
- "Sales Lead": a real potential customer/client asking about pricing, services, or interested in buying.
- "Support": a real customer asking a question or reporting an issue, not urgent.
- "Notification": automated emails from platforms/services — job boards (Indeed, Internshala, LinkedIn job alerts),
  newsletters, social media alerts, "your invoice is ready", shipping updates, calendar reminders, app/platform
  notifications, no-reply@ senders, marketing emails from companies, etc. These are machine-generated and do NOT
  need or expect a human reply.
- "Spam": unsolicited junk, phishing, scams, irrelevant cold outreach.
- "Other": anything real that doesn't fit above (e.g. personal email, internal FYI).

CRITICAL RULE: only "Urgent", "Sales Lead", and "Support" ever get a draft_reply. For every other category
(Notification, Spam, Other), draft_reply MUST be an empty string "" — do not write a reply to automated
notifications, job board alerts, newsletters, or company platform emails under any circumstances, even if the
content is technically addressed to the recipient.

If the sender address contains "noreply", "no-reply", "notifications@", "jobs@", "alerts@", or is clearly an
automated platform (Indeed, Internshala, LinkedIn, GitHub, Slack, Zoom, Calendly, etc.), it is almost always
"Notification" — not Urgent, not Support, not Sales Lead — regardless of the subject line wording.

Respond with ONLY valid JSON, no markdown, no extra text, in this exact format:
{{
  "category": one of {CATEGORIES},
  "reason": "one short sentence explaining why",
  "draft_reply": "a short, professional draft reply (2-4 sentences), or empty string per the rule above"
}}

From: {sender}
Subject: {subject}
Body:
{body[:3000]}
"""
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"category": "Other", "reason": "Could not parse AI response", "draft_reply": ""}


def create_draft_reply(service, to_addr, subject, body_text, thread_id, original_msg_id):
    message = MIMEText(body_text)
    message["to"] = to_addr
    message["subject"] = "Re: " + subject if not subject.lower().startswith("re:") else subject
    message["In-Reply-To"] = original_msg_id
    message["References"] = original_msg_id
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().drafts().create(userId="me", body={"message": {"raw": raw, "threadId": thread_id}}).execute()


def process_inbox(service):
    label_ids = {cat: get_or_create_label(service, LABEL_PREFIX + cat) for cat in CATEGORIES}
    results_out = []

    results = service.users().messages().list(
        userId="me", maxResults=MAX_EMAILS_TO_PROCESS, q="in:inbox"
    ).execute()
    messages = results.get("messages", [])

    for msg_meta in messages:
        msg = service.users().messages().get(userId="me", id=msg_meta["id"], format="full").execute()
        headers = msg["payload"]["headers"]
        sender = get_header(headers, "From")
        subject = get_header(headers, "Subject") or "(no subject)"
        message_id_header = get_header(headers, "Message-ID")
        body = get_email_text(msg["payload"])

        result = classify_and_draft(sender, subject, body)
        category = result.get("category", "Other")
        if category not in CATEGORIES:
            category = "Other"

        # Hard rule at the code level, not just the prompt: only these
        # categories are ever allowed to get a draft reply.
        DRAFT_ALLOWED_CATEGORIES = {"Urgent", "Sales Lead", "Support"}

        service.users().messages().modify(
            userId="me", id=msg_meta["id"], body={"addLabelIds": [label_ids[category]]}
        ).execute()

        if category in DRAFT_ALLOWED_CATEGORIES and result.get("draft_reply"):
            create_draft_reply(
                service, sender, subject, result["draft_reply"], msg["threadId"], message_id_header
            )

        results_out.append({
            "subject": subject,
            "category": category,
            "reason": result.get("reason", ""),
        })

    return results_out


# ---------- ROUTES ----------
@app.route("/")
def home():
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES, redirect_uri=url_for("oauth2callback", _external=True))
    auth_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    session["state"] = state
    session["code_verifier"] = flow.code_verifier
    return render_template_string(HOME_PAGE, auth_url=auth_url)


@app.route("/oauth2callback")
def oauth2callback():
    state = session.get("state")
    code_verifier = session.get("code_verifier")
    if not state or not code_verifier:
        return render_template_string(
            "<p>Session expired or link already used. <a href='/'>Click here to try again</a>.</p>"
        ), 400

    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES, state=state,
                                    redirect_uri=url_for("oauth2callback", _external=True))
    flow.code_verifier = code_verifier
    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception:
        return render_template_string(
            "<p>That login link was already used or expired. "
            "<a href='/'>Click here to start over</a> — just click Connect Gmail once and don't refresh.</p>"
        ), 400

    # Clear so the same code/state can't be replayed if the page is refreshed
    session.pop("state", None)
    session.pop("code_verifier", None)

    creds = flow.credentials

    service = build("gmail", "v1", credentials=creds)
    results = process_inbox(service)

    return render_template_string(RESULT_PAGE, count=len(results), results=results)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
