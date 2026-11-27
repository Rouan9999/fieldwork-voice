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
    """Two calendars per client:
    - roofer calendar (calendar_id): shared with the service account as
      "See only free/busy". Read-only; Workspace orgs often block more.
    - bookings calendar: the service account's OWN calendar. The agent writes
      each booking here and reads it back as busy, so it never double-books
      even though it cannot write to the roofer's calendar. The roofer can
      add it to his Google Calendar view (scripts/share_bookings_calendar.py).
    Credentials come from GOOGLE_SA_JSON (secret store only, never git)."""

    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self, calendar_id: str, sa_json: str | None = None, bookings_calendar_id: str | None = None):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        info = json.loads(sa_json or os.environ["GOOGLE_SA_JSON"])
        creds = service_account.Credentials.from_service_account_info(info, scopes=self.SCOPES)
        self._svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self.calendar_id = calendar_id
        # The service account's own primary calendar id is its email.
        self.bookings_id = bookings_calendar_id or os.getenv("BOOKINGS_CALENDAR_ID") or info["client_email"]

    async def busy(self, start: datetime, end: datetime):
        ids = [self.calendar_id] if self.calendar_id == self.bookings_id else [self.calendar_id, self.bookings_id]
        body = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": i} for i in ids]}
        resp = await asyncio.to_thread(lambda: self._svc.freebusy().query(body=body).execute())
        out = []
        for cid in ids:
            cal = resp["calendars"].get(cid, {})
            if cal.get("errors"):
                raise RuntimeError(f"calendar {cid} not reachable: {cal['errors']}")
            out += [
                (datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
                 datetime.fromisoformat(b["end"].replace("Z", "+00:00")))
                for b in cal.get("busy", [])
            ]
        return out

    async def create_event(self, start, end, summary, description, location):
        body = {
            "summary": summary,
            "description": description,
            "location": location,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        ev = await asyncio.to_thread(
            lambda: self._svc.events().insert(calendarId=self.bookings_id, body=body).execute()
        )
        return {"id": ev["id"], "link": ev.get("htmlLink", "")}
