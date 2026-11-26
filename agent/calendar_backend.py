"""Calendar backends. GoogleCalendar for real use, FakeCalendar for tests.

Both expose the same async API:
    busy(start, end) -> list[(start, end)]
    create_event(start, end, summary, description, location) -> dict(id, link)
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from itertools import count


class FakeCalendar:
    def __init__(self, busy: list[tuple[datetime, datetime]] | None = None):
        self.events: list[dict] = []
        self._busy = list(busy or [])
        self._ids = count(1)

    async def busy(self, start: datetime, end: datetime):
        out = [(s, e) for s, e in self._busy if s < end and start < e]
        out += [(ev["start"], ev["end"]) for ev in self.events if ev["start"] < end and start < ev["end"]]
        return out

    async def create_event(self, start, end, summary, description, location):
        ev = {
            "id": f"fake{next(self._ids)}",
            "start": start,
            "end": end,
            "summary": summary,
            "description": description,
            "location": location,
        }
        self.events.append(ev)
        return {"id": ev["id"], "link": ""}


class GoogleCalendar:
    """Service-account access to ONE shared calendar (client shares it with
    'Make changes to events'). Credentials come from GOOGLE_SA_JSON (the key
    file's JSON text, stored in the host's secret store, never in git)."""

    SCOPES = ["https://www.googleapis.com/auth/calendar.events",
              "https://www.googleapis.com/auth/calendar.freebusy"]

    def __init__(self, calendar_id: str, sa_json: str | None = None):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        info = json.loads(sa_json or os.environ["GOOGLE_SA_JSON"])
        creds = service_account.Credentials.from_service_account_info(info, scopes=self.SCOPES)
        self._svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self.calendar_id = calendar_id

    async def busy(self, start: datetime, end: datetime):
        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": self.calendar_id}],
        }
        resp = await asyncio.to_thread(lambda: self._svc.freebusy().query(body=body).execute())
        cal = resp["calendars"][self.calendar_id]
        if cal.get("errors"):
            raise RuntimeError(f"calendar not reachable: {cal['errors']}")
        return [
            (datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
             datetime.fromisoformat(b["end"].replace("Z", "+00:00")))
            for b in cal.get("busy", [])
        ]

    async def create_event(self, start, end, summary, description, location):
        body = {
            "summary": summary,
            "description": description,
            "location": location,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        ev = await asyncio.to_thread(
            lambda: self._svc.events().insert(calendarId=self.calendar_id, body=body).execute()
        )
        return {"id": ev["id"], "link": ev.get("htmlLink", "")}
