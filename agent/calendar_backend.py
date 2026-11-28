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


def sa_json_shape(raw: str) -> str:
    """Describe GOOGLE_SA_JSON without revealing it (length, first char, line count)."""
    raw = raw or ""
    t = raw.strip()
    first = t[:1]
    kind = "brace" if first == "{" else "quote" if first in "'\"" else "empty" if not first else "other"
    return f"len={len(raw)} first={kind} lines={raw.count(chr(10)) + 1 if raw else 0}"


def parse_sa_json(raw: str) -> dict:
    """Parse a service-account key pasted into a secret store.
    Accepts: raw JSON (one or many lines), JSON wrapped in quotes, or base64 of the JSON.
    Raises ValueError that names only the shape, never the content."""
    t = (raw or "").strip()
    candidates = [t]
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"":
        t = t[1:-1].strip()
        candidates.append(t)
    if t and not t.startswith("{"):
        try:
            import base64
            candidates.append(base64.b64decode(t + "=" * (-len(t) % 4)).decode("utf-8").strip())
        except Exception:
            pass
    for c in candidates:
        try:
            info = json.loads(c, strict=False)  # strict=False: tolerate real newlines inside private_key
        except ValueError:
            continue
        if isinstance(info, str):  # double-encoded JSON
            try:
                info = json.loads(info, strict=False)
            except ValueError:
                continue
        if isinstance(info, dict) and info.get("client_email") and info.get("private_key"):
            return info
    raise ValueError(f"GOOGLE_SA_JSON is not a usable service-account key ({sa_json_shape(raw)})")


class UnavailableCalendar:
    """Used when the real calendar cannot start. The call still goes on:
    tools fail softly and the agent takes a message instead of booking."""

    def __init__(self, reason: str):
        self.reason = reason

    async def busy(self, start, end):
        raise RuntimeError(f"calendar unavailable: {self.reason}")

    async def create_event(self, *a, **k):
        raise RuntimeError(f"calendar unavailable: {self.reason}")


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

        info = parse_sa_json(sa_json or os.environ.get("GOOGLE_SA_JSON", ""))
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
