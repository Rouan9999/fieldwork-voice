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
res = svc.acl().insert(calendarId=info["client_email"], body=rule, sendNotifications=True).execute()
print(f"shared bookings calendar {info['client_email']} with {owner} as {res['role']}")
