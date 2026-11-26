"""Pure slot logic: business hours + busy times -> open inspection slots.

No network. Everything is timezone-aware; the client's timezone decides hours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .config import DAYS, ClientConfig


@dataclass(frozen=True)
class Slot:
    start: datetime  # aware, client tz
    end: datetime

    def spoken(self) -> str:
        return spoken_time(self.start)


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def spoken_time(dt: datetime) -> str:
    """'Thursday, December 3rd at 10 AM' / '... at 1:30 PM'."""
    hour12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    t = f"{hour12} {ampm}" if dt.minute == 0 else f"{hour12}:{dt.minute:02d} {ampm}"
    return f"{dt.strftime('%A')}, {dt.strftime('%B')} {_ordinal(dt.day)} at {t}"


def _overlaps(a0, a1, b0, b1) -> bool:
    return a0 < b1 and b0 < a1


def open_slots(
    cfg: ClientConfig,
    busy: list[tuple[datetime, datetime]],
    now: datetime,
    only_date: date | None = None,
) -> list[Slot]:
    """All bookable slots from now to now+days_ahead, honoring hours, buffer, lead time."""
    tz = cfg.tz
    now = now.astimezone(tz)
    earliest = now + timedelta(hours=cfg.min_lead_hours)
    length = timedelta(minutes=cfg.inspection_minutes)
    buf = timedelta(minutes=cfg.buffer_minutes)
    step = timedelta(minutes=cfg.slot_step_minutes)
    busy_tz = [(b0.astimezone(tz) - buf, b1.astimezone(tz) + buf) for b0, b1 in busy]

    out: list[Slot] = []
    for i in range(cfg.days_ahead + 1):
        d = (now + timedelta(days=i)).date()
        if only_date and d != only_date:
            continue
        hours = cfg.hours[DAYS[d.weekday()]]
        if not hours:
            continue
        open_t = time.fromisoformat(hours[0])
        close_t = time.fromisoformat(hours[1])
        # Build in local wall time; ZoneInfo handles DST.
        cur = datetime.combine(d, open_t, tzinfo=tz)
        close = datetime.combine(d, close_t, tzinfo=tz)
        while cur + length <= close:
            end = cur + length
            if cur >= earliest and not any(_overlaps(cur, end, b0, b1) for b0, b1 in busy_tz):
                out.append(Slot(cur, end))
            cur += step
    return out


def pick_offers(
    slots: list[Slot], part_of_day: str | None = None, n: int = 3
) -> list[Slot]:
    """Choose 2-3 spread-out options instead of dumping the whole list.

    Rule: earliest slot, then one at least 3 hours later (same or next day),
    then the first slot on a different day. Filter by morning/afternoon first.
    """
    if part_of_day in ("morning", "afternoon"):
        want_morning = part_of_day == "morning"
        filtered = [s for s in slots if (s.start.hour < 12) == want_morning]
        if filtered:
            slots = filtered
    if not slots:
        return []
    picks = [slots[0]]
    for s in slots[1:]:
        if len(picks) >= n:
            break
        if all(abs(s.start - p.start) >= timedelta(hours=3) for p in picks):
            if len(picks) == 1 or s.start.date() not in {p.start.date() for p in picks}:
                picks.append(s)
    # Fill if spreading left us short.
    for s in slots:
        if len(picks) >= n:
            break
        if s not in picks and all(abs(s.start - p.start) >= timedelta(hours=1) for p in picks):
            picks.append(s)
    return sorted(picks, key=lambda s: s.start)[:n]


def day_table(cfg: ClientConfig, now: datetime) -> str:
    """Lines like 'Thursday = 2026-11-26' so the model never guesses dates."""
    now = now.astimezone(cfg.tz)
    lines = []
    for i in range(cfg.days_ahead + 1):
        d = (now + timedelta(days=i)).date()
        label = "today" if i == 0 else ("tomorrow" if i == 1 else "")
        lines.append(f"- {d.strftime('%A %B')} {d.day} = {d.isoformat()} {label}".rstrip())
    return "\n".join(lines)
