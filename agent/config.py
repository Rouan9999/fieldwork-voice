"""Per-client config. One JSON file per client; nothing else is client-specific."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class ClientConfig:
    client_id: str
    business_name: str
    timezone: str
    hours: dict
    services: list
    service_area: str
    owner_cell: str
    calendar_id: str
    agent_name: str = "the assistant"
    faq: list = field(default_factory=list)
    inspection_minutes: int = 60
    buffer_minutes: int = 30
    min_lead_hours: int = 2
    days_ahead: int = 7
    slot_step_minutes: int = 30
    owner_name: str = "the owner"
    owner_email: str = ""
    twilio_number: str = ""
    escalation: dict = field(default_factory=dict)
    recording_enabled: bool = False
    voice: str = "catherine"  # preset name from agent/voices.py
    ai_disclosure: str = "greeting"  # "greeting" = say it up front; "on_ask" = only when asked (never deny)
    max_call_minutes: int = 10

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def validate(self) -> None:
        ZoneInfo(self.timezone)  # raises if bad
        for d in DAYS:
            if d not in self.hours:
                raise ValueError(f"hours missing day {d}")
            v = self.hours[d]
            if v is not None and (len(v) != 2 or v[0] >= v[1]):
                raise ValueError(f"bad hours for {d}: {v}")
        if self.ai_disclosure not in ("greeting", "on_ask"):
            raise ValueError("ai_disclosure must be 'greeting' or 'on_ask'")
        if self.inspection_minutes <= 0:
            raise ValueError("inspection_minutes must be > 0")


def load_config(path: str | None = None) -> ClientConfig:
    path = path or os.getenv("CLIENT_CONFIG", "clients/test-roofing.json")
    data = json.loads(Path(path).read_text())
    cfg = ClientConfig(**{k: v for k, v in data.items() if not k.startswith('_')})
    # Phone numbers can be overridden from the secret store so they stay out of git.
    cfg.owner_cell = os.getenv("OWNER_CELL", cfg.owner_cell)
    cfg.twilio_number = os.getenv("TWILIO_NUMBER", cfg.twilio_number)
    cfg.calendar_id = os.getenv("CALENDAR_ID", cfg.calendar_id)
    cfg.validate()
    return cfg
