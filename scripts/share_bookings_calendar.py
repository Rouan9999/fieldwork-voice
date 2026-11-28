"""Let the roofer SEE the agent's bookings inside his own Google Calendar.

Gives OWNER_EMAIL read access to the service account's bookings calendar.
The roofer then adds it once: Google Calendar > Other calendars > + >
Subscribe to calendar > paste the service account email.
    OWNER_EMAIL=rouan@fieldworkmedia.agency python scripts/share_bookings_calendar.py
"""

import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

info = json.loads(os.environ["GOOGLE_SA_JSON"])
creds = service_account.Credentials.from_service_account_info(
    info, scopes=["https://www.googleapis.com/auth/calendar"])
svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
owner = os.environ["OWNER_EMAIL"]
rule = {"role": "reader", "scope": {"type": "user", "value": owner}}
try:
    res = svc.acl().insert(calendarId=info["client_email"], body=rule, sendNotifications=True).execute()
    print(f"shared bookings calendar with {owner} as {res['role']}")
except Exception as e:
    print(f"share step failed: {str(e)[-160:]}")
for r in svc.acl().list(calendarId=info["client_email"]).execute().get("items", []):
    print("acl:", r["role"], r["scope"].get("type"), r["scope"].get("value", ""))

# Proof the agent's bookings landed: list upcoming events on the bookings calendar.
from datetime import datetime, timezone
items = svc.events().list(calendarId=info["client_email"], timeMin=datetime.now(timezone.utc).isoformat(),
                          singleEvents=True, orderBy="startTime", maxResults=10).execute().get("items", [])
print(f"upcoming bookings: {len(items)}")
for e in items:
    print(" -", e["start"].get("dateTime", e["start"].get("date")), "|", e.get("summary", ""))
