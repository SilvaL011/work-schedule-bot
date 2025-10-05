import os
import json
import logging
import boto3
from botocore.exceptions import ClientError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import re
from zoneinfo import ZoneInfo
from dateutil import parser as dtparse
import base64
from bs4 import BeautifulSoup
import hashlib
from datetime import datetime, timedelta

log = logging.getLogger(__name__)
SUBJECT_FILTER = os.environ.get("SUBJECT_FILTER", "Publish Schedule Notification")
SHIFT_TITLE    = os.environ.get("SHIFT_TITLE", "Work")
SECRET_NAME = os.environ.get("SECRET_NAME", "work-schedule-bot")
EVENT_COLOR_ID = os.getenv("EVENT_COLOR_ID", "6")  # Tangerine (orange-ish in Google’s palette)
NUM_PUBLISH_EMAILS = int(os.getenv("NUM_PUBLISH_EMAILS", "5"))



def _load_secret():
    #Read JSON config from AWS Secrets Manager and return it as a dict.
    #Expects one JSON object (the one you saved earlier).

    region = os.getenv("AWS_REGION", "ca-central-1")  # use your region; CLI sets this when you run in Lambda
    try:
        client = boto3.session.Session().client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=SECRET_NAME)
    except ClientError as e:
        log.error(f"Failed to read secret {SECRET_NAME} in {region}: {e}")
        raise

    if "SecretString" in resp and resp["SecretString"]:
        return json.loads(resp["SecretString"])
    if "SecretBinary" in resp and resp["SecretBinary"]:
        return json.loads(resp["SecretBinary"].decode("utf-8"))
    raise RuntimeError("Secret had no SecretString or SecretBinary")

def _google_creds(secret: dict) -> Credentials:
    #Turn the refresh_token + client id/secret into OAuth credentials.
    #google-auth will auto-exchange the refresh token for a short-lived access token.

    return Credentials(
        token=None,
        refresh_token=secret["refresh_token"],
        token_uri=secret["token_uri"],
        client_id=secret["client_id"],
        client_secret=secret["client_secret"],
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/calendar",
        ],
    )

def _build_google_clients(creds):
    #Create service objects for Gmail and Calendar.

    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    gcal  = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return gmail, gcal

def _latest_published_message(gmail, sender_filter: str, days: int = 30):
    """Get the most recent 'Publish Schedule Notification' email from your sender."""
    msgs = _gmail_messages(gmail, sender_filter, days=days, max_results=1)
    return msgs[0] if msgs else None

def _process_latest_email(gmail, gcal, calendar_id: str, tz: str, sender_filter: str):
    """Parse the latest published schedule and create/update events."""
    m = _latest_published_message(gmail, sender_filter)
    if not m:
        return 0, 0  # created, updated

    html = _get_message_html(gmail, m["id"])
    shifts = _parse_synerion_table(html, tz)

    created = updated = 0
    for s in shifts:
        status = _upsert_event(gcal, calendar_id, s)
        if status == "created":
            created += 1
        elif status == "updated":
            updated += 1
    return created, updated

def _recent_published_messages(gmail, sender_filter: str, days: int = 30, limit: int = 5):
    """
    Return up to `limit` most-recent schedule publish emails.
    We process oldest→newest to keep behavior stable.
    """
    msgs = _gmail_messages(gmail, sender_filter, days=days, max_results=limit)
    return list(reversed(msgs))  # oldest first

def _process_recent_emails(gmail, gcal, calendar_id: str, tz: str, sender_filter: str, limit: int = 5):
    """Parse the last `limit` published schedules and upsert shifts for each."""
    created = updated = 0
    msgs = _recent_published_messages(gmail, sender_filter, days=30, limit=limit)
    for m in msgs:
        html = _get_message_html(gmail, m["id"])
        shifts = _parse_synerion_table(html, tz)
        for s in shifts:
            status = _upsert_event(gcal, calendar_id, s)
            if status == "created":
                created += 1
            elif status == "updated":
                updated += 1
    return created, updated



def handler(event, context):
    """Weekly run: read the last few published schedules and upsert all shifts."""
    cfg = _load_secret()
    creds = _google_creds(cfg)
    gmail, gcal = _build_google_clients(creds)

    created, updated = _process_recent_emails(
        gmail=gmail,
        gcal=gcal,
        calendar_id=cfg.get("calendar_id", "primary"),
        tz=cfg.get("timezone", "America/Toronto"),
        sender_filter=cfg["sender_filter"],
        limit=NUM_PUBLISH_EMAILS,
    )
    return {"ok": True, "created": created, "updated": updated}


def _gmail_messages(gmail, sender_filter: str, days: int = 30, max_results: int = 10):
    """
    Return recent messages from the scheduling sender that match the exact subject we want.
    Uses Gmail's search syntax: newer_than:<Nd>, from:, subject:"...".
    """
    q = f'newer_than:{days}d from:{sender_filter} subject:"{SUBJECT_FILTER}"'
    res = gmail.users().messages().list(userId="me", q=q, maxResults=max_results).execute()
    return res.get("messages", []) or []

def _get_message_html(gmail, msg_id: str) -> str:
    """Return the email body as HTML (prefer HTML part; else text)."""
    m = gmail.users().messages().get(userId="me", id=msg_id, format="full").execute()
    payload = m.get("payload", {})
    parts = payload.get("parts", [])

    def _dec(b64: str) -> str:
        return base64.urlsafe_b64decode(b64.encode()).decode(errors="ignore")

    # try HTML first
    for p in parts or []:
        if p.get("mimeType") == "text/html" and p.get("body", {}).get("data"):
            return _dec(p["body"]["data"])

    # fallback to any body
    if parts:
        for p in parts:
            data = p.get("body", {}).get("data")
            if data:
                return _dec(data)

    data = payload.get("body", {}).get("data")
    return _dec(data) if data else ""

def _parse_synerion_table(html: str, tz: str):
    """
    Parse Synerion schedule table.
    - derive the year from the header "published from mm/dd/yyyy to ..."
    - read rows Date | Time | Department | Job
    - skip "Day off"
    - summary = "Work Shift" (no department)
    - no location (set to None)
    """
    soup = BeautifulSoup(html, "html.parser")
    text_all = soup.get_text(" ", strip=True)

    # year from header line
    year = None
    m = re.search(r"published\s+from\s+(\d{1,2}/\d{1,2}/\d{4})\s+to\s+(\d{1,2}/\d{1,2}/\d{4})", text_all, re.I)
    if m:
        year = dtparse.parse(m.group(1)).year
    if not year:
        year = datetime.now(ZoneInfo(tz)).year

    # find the table by headers
    target = None
    for tbl in soup.find_all("table"):
        ths = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if {"date","time","department","job"}.issubset(set(ths)):
            target = tbl
            break
    if not target:
        return []

    shifts = []
    tbody = target.find("tbody") or target
    tzinfo = ZoneInfo(tz)

    for tr in tbody.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) < 2:
            continue

        date_cell = tds[0]            # e.g., "Tue 10/07"
        time_cell = tds[1]            # e.g., "12:00 - 20:15" or "Day off"

        # skip days off
        if re.search(r"\bday\s*off\b", time_cell, re.I):
            continue

        # mm/dd from the date cell
        dm = re.search(r"(\d{1,2})/(\d{1,2})", date_cell)
        if not dm:
            continue
        mm, dd = int(dm.group(1)), int(dm.group(2))

        # normalize separators: handle "-", "–", with/without spaces
        norm = time_cell.replace("–", "-").replace("—", "-")
        # remove extra spaces around the dash
        norm = re.sub(r"\s*-\s*", "-", norm)
        # split into start/end
        parts = norm.split("-")
        if len(parts) != 2:
            continue
        start_t = parts[0].strip()
        end_t   = parts[1].strip()

        try:
            start_dt = dtparse.parse(f"{year}-{mm:02d}-{dd:02d} {start_t}").replace(tzinfo=tzinfo)
            end_dt   = dtparse.parse(f"{year}-{mm:02d}-{dd:02d} {end_t}").replace(tzinfo=tzinfo)
        except Exception:
            # if dateutil ever chokes, skip this row
            continue

        shifts.append({
            "start": start_dt,
            "end": end_dt,
            "summary": SHIFT_TITLE,
            "location": None,   # department dropped per your preference
        })

    return shifts

def _hash_shift(s: dict) -> str:
    """Stable fingerprint so repeated runs don't create duplicates."""
    key = f"{s['start'].date()}|{s['start'].time()}|{s['end'].time()}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]

def _find_existing(calendar, calendar_id: str, h: str, start_dt):
    """Look for an event on that day with the same private hash."""
    time_min = datetime(start_dt.year, start_dt.month, start_dt.day, tzinfo=start_dt.tzinfo)
    time_max = time_min + timedelta(days=1)
    res = calendar.events().list(
        calendarId=calendar_id,
        privateExtendedProperty=f"hash={h}",
        timeMin=time_min.isoformat(),
        timeMax=time_max.isoformat(),
        singleEvents=True,
    ).execute()
    items = res.get("items", [])
    return items[0] if items else None

def _has_overlap(calendar, calendar_id: str, start_dt, end_dt, title_hint: str) -> bool:
    """
    Return True if any existing event overlaps [start_dt, end_dt) and
    its title looks like the one we would create (to avoid skipping unrelated events).
    """
    res = calendar.events().list(
        calendarId=calendar_id,
        timeMin=start_dt.isoformat(),
        timeMax=end_dt.isoformat(),
        singleEvents=True,
    ).execute()

    for it in res.get("items", []):
        summary = (it.get("summary") or "").strip()
        if summary.lower().startswith(title_hint.lower()):
            return True
    return False


def _upsert_event(calendar, calendar_id: str, shift: dict) -> str:
    """Create/update by hash; otherwise skip if a manual overlapping event already exists."""
    h = _hash_shift(shift)
    existing = _find_existing(calendar, calendar_id, h, shift["start"])

    body = {
        "summary": shift["summary"],                       # e.g., "Work"
        "start": {"dateTime": shift["start"].isoformat()},
        "end":   {"dateTime": shift["end"].isoformat()},
        "extendedProperties": {"private": {"hash": h}},
        "reminders": {"useDefault": True},
        "colorId": EVENT_COLOR_ID,                        # keep your orange color
    }

    if existing:
        calendar.events().update(calendarId=calendar_id, eventId=existing["id"], body=body).execute()
        return "updated"

    # No hashed match → before creating, skip if a manual event already overlaps this window.
    if _has_overlap(calendar, calendar_id, shift["start"], shift["end"], title_hint=shift["summary"]):
        return "skipped_overlap"

    calendar.events().insert(calendarId=calendar_id, body=body).execute()
    return "created"



def _preview_shifts(shifts):
    for s in shifts:
        day = s["start"].strftime("%a %m/%d")
        window = f"{s['start'].strftime('%H:%M')}–{s['end'].strftime('%H:%M')}"
        print(f"- {day} {window}  |  {s['summary']}")

"""
#Commented out main used for testing
if __name__ == "__main__":
    # Your secret is in us-east-2; keep this aligned with where the secret lives.
    os.environ.setdefault("AWS_REGION", "us-east-2")

    cfg = _load_secret()
    print("Loaded secret keys:", sorted(cfg.keys()))
    creds = _google_creds(cfg)
    gmail, gcal = _build_google_clients(creds)
    print("Google client objects built OK")

    # Only consider the latest "Publish Schedule Notification"
    msgs = _gmail_messages(gmail, cfg["sender_filter"], days=30, max_results=1)
    print(f"emails_count: {len(msgs)}")
    if not msgs:
        print("No messages found. Try increasing 'days' or verify sender_filter.")
        raise SystemExit(0)

    # Parse and preview
    html = _get_message_html(gmail, msgs[0]["id"])
    shifts = _parse_synerion_table(html, cfg.get("timezone", "America/Toronto"))
    print(f"parsed_shifts: {len(shifts)}")
    _preview_shifts(shifts)

    # Safe dry-run vs write-one test
    if DO_WRITE_ONE and shifts:
        status = _upsert_event(gcal, cfg["calendar_id"], shifts[0])
        print(f"[WRITE-ONE] first shift -> {status}")   # created | updated | skipped_overlap
    else:
        for s in shifts:
            print(f"[DRY-RUN] {s['start'].strftime('%a %m/%d %H:%M')}–{s['end'].strftime('%H:%M')} -> {s['summary']}")

"""