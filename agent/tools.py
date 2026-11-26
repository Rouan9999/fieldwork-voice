"""The 5 brain tools. Framework-free core (testable) + JSON schemas.

Guard rails live HERE, not only in the prompt:
- book() only accepts a slot_id that check_slots offered in this call.
- book() re-checks the calendar right before inserting (no double booking).
- book() requires confirmed_by_caller=true (details were read back).
- Card numbers / SSNs are scrubbed from anything stored or texted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from loguru import logger

from .config import ClientConfig
from .messaging import SafetyBlock
from .slots import open_slots, pick_offers, spoken_time

_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SSN = re.compile(r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b")


def scrub(text: str) -> str:
    text = _CARD.sub("[removed number]", text or "")
    return _SSN.sub("[removed number]", text)


def normalize_phone(p: str) -> str:
    digits = re.sub(r"\D", "", p or "")
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return ""


def calendar_link(title: str, start: datetime, end: datetime, details: str, location: str) -> str:
    fmt = "%Y%m%dT%H%M%SZ"
    s = start.astimezone(timezone.utc).strftime(fmt)
    e = end.astimezone(timezone.utc).strftime(fmt)
    return (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={quote(title)}&dates={s}/{e}&details={quote(details)}&location={quote(location)}"
    )


@dataclass
class CallState:
    call_sid: str = ""
    caller_number: str = ""
    offers: dict = field(default_factory=dict)  # slot_id -> Slot
    booking: dict | None = None
    messages: list = field(default_factory=list)
    transferred: bool = False
    texts_sent: list = field(default_factory=list)
    errors: list = field(default_factory=list)


class AgentTools:
    def __init__(self, cfg: ClientConfig, calendar, twilio, state: CallState, now_fn=None):
        self.cfg = cfg
        self.cal = calendar
        self.tw = twilio
        self.st = state
        self.now = now_fn or (lambda: datetime.now(cfg.tz))

    # 1 ---------------------------------------------------------------
    async def check_slots(self, preferred_date: str | None = None, part_of_day: str | None = None) -> dict:
        now = self.now()
        only = None
        if preferred_date:
            try:
                only = date.fromisoformat(preferred_date)
            except ValueError:
                return {"ok": False, "error": "preferred_date must be YYYY-MM-DD"}
        horizon = now + timedelta(days=self.cfg.days_ahead + 1)
        try:
            busy = await self.cal.busy(now, horizon)
        except Exception as e:  # calendar down -> take a message instead
            logger.error(f"calendar busy failed: {e}")
            self.st.errors.append(f"calendar: {e}")
            return {"ok": False, "error": "calendar unavailable", "say": "take a message instead"}
        slots = open_slots(self.cfg, busy, now, only_date=only)
        if not slots and only:
            slots = open_slots(self.cfg, busy, now)
            note = "no openings that day; these are the nearest"
        else:
            note = ""
        offers = pick_offers(slots, part_of_day)
        self.st.offers = {}
        out = []
        for i, s in enumerate(offers):
            sid = "ABC"[i]
            self.st.offers[sid] = s
            out.append({"slot_id": sid, "time": s.spoken()})
        return {"ok": True, "slots": out, "note": note or None}

    # 2 ---------------------------------------------------------------
    async def book(
        self,
        slot_id: str,
        name: str,
        phone: str,
        address: str,
        issue: str,
        insurance_claim: bool,
        confirmed_by_caller: bool,
        urgency: str = "normal",
    ) -> dict:
        if self.st.booking:
            return {"ok": False, "error": "already booked on this call", "booking": self.st.booking["time"]}
        if not confirmed_by_caller:
            return {"ok": False, "error": "read the details back and get a yes first"}
        slot = self.st.offers.get((slot_id or "").strip().upper())
        if not slot:
            return {"ok": False, "error": "unknown slot_id; call check_slots and offer those times"}
        missing = [k for k, v in {"name": name, "address": address, "issue": issue}.items() if not (v or "").strip()]
        phone_e164 = normalize_phone(phone) or normalize_phone(self.st.caller_number)
        if not phone_e164:
            missing.append("phone")
        if missing:
            return {"ok": False, "error": f"missing: {', '.join(missing)}"}
        # Re-check right before writing: someone else may have taken it.
        busy = await self.cal.busy(slot.start - timedelta(minutes=self.cfg.buffer_minutes),
                                   slot.end + timedelta(minutes=self.cfg.buffer_minutes))
        if busy:
            self.st.offers.pop(slot_id.upper(), None)
            return {"ok": False, "error": "that time was just taken; call check_slots again"}
        name, address, issue = scrub(name), scrub(address), scrub(issue)
        desc = (
            f"Booked by AI assistant.\nName: {name}\nPhone: {phone_e164}\nAddress: {address}\n"
            f"Issue: {issue}\nInsurance claim: {'yes' if insurance_claim else 'no'}\nUrgency: {urgency}\n"
            f"Call SID: {self.st.call_sid}"
        )
        ev = await self.cal.create_event(
            slot.start, slot.end, f"Roof inspection - {name}", desc, address
        )
        self.st.booking = {
            "event_id": ev["id"], "time": slot.spoken(), "start": slot.start.isoformat(),
            "end": slot.end.isoformat(), "name": name, "phone": phone_e164,
            "address": address, "issue": issue, "insurance_claim": insurance_claim, "urgency": urgency,
        }
        return {"ok": True, "booked": slot.spoken()}

    # 3 ---------------------------------------------------------------
    async def send_confirmation_text(self) -> dict:
        b = self.st.booking
        if not b:
            return {"ok": False, "error": "nothing booked yet"}
        start = datetime.fromisoformat(b["start"])
        end = datetime.fromisoformat(b["end"])
        link = calendar_link(f"Roof inspection - {self.cfg.business_name}", start, end,
                             f"Free roof inspection with {self.cfg.business_name}.", b["address"])
        body = (
            f"{self.cfg.business_name}: your free roof inspection is booked for {spoken_time(start)} "
            f"at {b['address']}. Add to calendar: {link} Need to change it? Call or text this number. "
            "Reply STOP to opt out."
        )
        try:
            sid = await self.tw.send_sms(b["phone"], self.cfg.twilio_number, body)
        except SafetyBlock as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            self.st.errors.append(f"sms: {e}")
            return {"ok": False, "error": "text failed; tell the caller the office will text them"}
        self.st.texts_sent.append(sid)
        return {"ok": True}

    # 4 ---------------------------------------------------------------
    async def take_message(self, name: str, phone: str, reason: str, urgency: str = "normal") -> dict:
        msg = {
            "name": scrub(name), "phone": normalize_phone(phone) or normalize_phone(self.st.caller_number) or scrub(phone),
            "reason": scrub(reason), "urgency": urgency if urgency in ("normal", "urgent", "emergency") else "normal",
            "at": self.now().isoformat(),
        }
        self.st.messages.append(msg)
        return {"ok": True, "saved": True}

    # 5 ---------------------------------------------------------------
    async def transfer(self, reason: str) -> dict:
        if self.st.transferred:
            return {"ok": False, "error": "already transferring"}
        if not self.st.call_sid:
            return {"ok": False, "error": "no live call; take a message"}
        try:
            await self.tw.transfer(
                self.st.call_sid, self.cfg.owner_cell, self.cfg.twilio_number,
                "Connecting you now. One moment.",
            )
        except Exception as e:
            self.st.errors.append(f"transfer: {e}")
            return {"ok": False, "error": "transfer failed; take a message and say the owner will call back"}
        self.st.transferred = True
        self.st.messages.append({"reason": f"Transferred: {scrub(reason)}", "urgency": "urgent",
                                 "phone": self.st.caller_number, "name": "", "at": self.now().isoformat()})
        return {"ok": True}


SCHEMAS = [
    {
        "name": "check_slots",
        "description": "Get 2-3 open inspection times from the real calendar. Always call this before offering times. Never invent times.",
        "properties": {
            "preferred_date": {"type": "string", "description": "YYYY-MM-DD if the caller named a day, from the day table"},
            "part_of_day": {"type": "string", "enum": ["morning", "afternoon"]},
        },
        "required": [],
    },
    {
        "name": "book",
        "description": "Book one offered slot after reading all details back and the caller said yes.",
        "properties": {
            "slot_id": {"type": "string", "description": "A, B or C from check_slots"},
            "name": {"type": "string"},
            "phone": {"type": "string", "description": "callback number; use the caller ID if they confirm it"},
            "address": {"type": "string", "description": "street, city"},
            "issue": {"type": "string", "description": "short description of the roof problem"},
            "insurance_claim": {"type": "boolean"},
            "urgency": {"type": "string", "enum": ["normal", "urgent", "emergency"]},
            "confirmed_by_caller": {"type": "boolean", "description": "true only after read-back and a clear yes"},
        },
        "required": ["slot_id", "name", "phone", "address", "issue", "insurance_claim", "confirmed_by_caller"],
    },
    {
        "name": "send_confirmation_text",
        "description": "Text the caller the booked date, time, address and calendar link. Call right after a successful book.",
        "properties": {},
        "required": [],
    },
    {
        "name": "take_message",
        "description": "Save a message for the owner when you cannot book (existing job, question you cannot answer, caller wants a callback, after-hours).",
        "properties": {
            "name": {"type": "string"},
            "phone": {"type": "string"},
            "reason": {"type": "string"},
            "urgency": {"type": "string", "enum": ["normal", "urgent", "emergency"]},
        },
        "required": ["name", "phone", "reason"],
    },
    {
        "name": "transfer",
        "description": "Connect the caller to a person. Use when they ask for a human, are upset, or there is an active emergency (water coming in now, tree on roof). Take a message first if you can do it quickly.",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
    },
]
