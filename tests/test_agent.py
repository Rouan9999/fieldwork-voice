import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from agent.calendar_backend import FakeCalendar
from agent.config import load_config
from agent.messaging import SafetyBlock, _allowed
from agent.prompt import greeting, is_open, system_prompt
from agent.report import CallMetrics, owner_summary
from agent.slots import open_slots, pick_offers, spoken_time
from agent.tools import AgentTools, CallState, normalize_phone, scrub

CT = ZoneInfo("America/Chicago")
CFG = load_config("clients/test-roofing.json")


def run(c):
    return asyncio.get_event_loop().run_until_complete(c) if False else asyncio.run(c)


class FakeTwilio:
    def __init__(self, fail=False):
        self.sent, self.transfers, self.fail = [], [], fail

    async def send_sms(self, to, from_, body):
        if self.fail:
            raise RuntimeError("boom")
        if not _allowed(to):
            raise SafetyBlock("blocked")
        self.sent.append((to, body))
        return "SM1"

    async def transfer(self, call_sid, to, caller_id, say):
        self.transfers.append((call_sid, to))


def make(now, busy=None, tw=None, caller="+12055550100"):
    st = CallState(call_sid="CA123", caller_number=caller)
    cal = FakeCalendar(busy)
    return AgentTools(CFG, cal, tw or FakeTwilio(), st, now_fn=lambda: now), cal, st


# Wed Nov 25 2026, 12:30 PM CT
NOW = datetime(2026, 11, 25, 12, 30, tzinfo=CT)


def test_slots_respect_hours_lead_and_closed_sunday():
    slots = open_slots(CFG, [], NOW)
    assert slots[0].start == datetime(2026, 11, 25, 14, 30, tzinfo=CT)  # 2h lead
    for s in slots:
        assert s.start.weekday() != 6  # Sunday closed
        h = CFG.hours[["mon", "tue", "wed", "thu", "fri", "sat", "sun"][s.start.weekday()]]
        assert s.start.strftime("%H:%M") >= h[0] and s.end.strftime("%H:%M") <= h[1]


def test_busy_plus_buffer_blocks():
    busy = [(datetime(2026, 11, 26, 10, 0, tzinfo=CT), datetime(2026, 11, 26, 11, 0, tzinfo=CT))]
    slots = open_slots(CFG, busy, NOW)
    starts = {s.start for s in slots}
    for blocked in ["09:00", "09:30", "10:00", "10:30", "11:00"]:
        h, m = map(int, blocked.split(":"))
        assert datetime(2026, 11, 26, h, m, tzinfo=CT) not in starts
    assert datetime(2026, 11, 26, 11, 30, tzinfo=CT) in starts  # 30 min buffer after
    assert datetime(2026, 11, 26, 8, 30, tzinfo=CT) in starts  # ends 9:30 = buffer edge


def test_busy_given_in_utc_is_converted():
    # 16:00Z = 10:00 CT
    from datetime import timezone
    busy = [(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc), datetime(2026, 11, 26, 17, 0, tzinfo=timezone.utc))]
    starts = {s.start for s in open_slots(CFG, busy, NOW)}
    assert datetime(2026, 11, 26, 10, 0, tzinfo=CT) not in starts


def test_dst_change_keeps_wall_clock_hours():
    # DST ends Nov 1 2026; check a week spanning it.
    now = datetime(2026, 10, 30, 7, 0, tzinfo=CT)
    slots = open_slots(CFG, [], now)
    assert all(s.start.hour >= 8 for s in slots)
    assert any(s.start.date().isoformat() == "2026-11-02" and s.start.hour == 8 for s in slots)


def test_offers_are_2_to_3_and_spread():
    offers = pick_offers(open_slots(CFG, [], NOW))
    assert 2 <= len(offers) <= 3
    gaps = [b.start - a.start for a, b in zip(offers, offers[1:])]
    assert all(g >= timedelta(hours=1) for g in gaps)


def test_part_of_day_filter():
    offers = pick_offers(open_slots(CFG, [], NOW), "morning")
    assert offers and all(o.start.hour < 12 for o in offers)


def test_spoken_time():
    assert spoken_time(datetime(2026, 12, 3, 10, 0, tzinfo=CT)) == "Thursday, December 3rd at 10 AM"
    assert spoken_time(datetime(2026, 12, 11, 13, 30, tzinfo=CT)) == "Friday, December 11th at 1:30 PM"


def test_full_booking_flow_and_no_double_booking(monkeypatch):
    monkeypatch.setenv("TEST_MODE", "1")
    monkeypatch.setenv("ALLOWED_TEST_NUMBERS", "+12055550100")
    tw = FakeTwilio()
    t, cal, st = make(NOW, tw=tw)
    r = run(t.check_slots())
    assert r["ok"] and len(r["slots"]) >= 2
    sid = r["slots"][0]["slot_id"]
    # refuses without read-back
    assert not run(t.book(sid, "Jane Doe", "205-555-0100", "1 Main St, Tuscaloosa", "leak", True, False))["ok"]
    # refuses invented slot
    assert not run(t.book("Z", "Jane Doe", "205-555-0100", "1 Main St", "leak", True, True))["ok"]
    ok = run(t.book(sid, "Jane Doe", "205-555-0100", "1 Main St, Tuscaloosa", "leak", True, True))
    assert ok["ok"], ok
    assert len(cal.events) == 1 and cal.events[0]["location"] == "1 Main St, Tuscaloosa"
    assert run(t.send_confirmation_text())["ok"]
    to, body = tw.sent[0]
    assert to == "+12055550100" and "calendar.google.com" in body and "STOP" in body
    # second caller cannot get the same time
    t2, _, _ = make(NOW)
    t2.cal = cal
    r2 = run(t2.check_slots())
    assert all(s["time"] != ok["booked"] for s in r2["slots"])


def test_slot_taken_between_offer_and_book():
    t, cal, st = make(NOW)
    r = run(t.check_slots())
    slot = st.offers["A"]
    cal._busy.append((slot.start, slot.end))  # someone else grabs it
    res = run(t.book("A", "Jane", "2055550100", "1 Main St", "leak", False, True))
    assert not res["ok"] and "taken" in res["error"]
    assert len(cal.events) == 0


def test_test_mode_blocks_unknown_numbers(monkeypatch):
    monkeypatch.setenv("TEST_MODE", "1")
    monkeypatch.setenv("ALLOWED_TEST_NUMBERS", "+12055550100")
    t, cal, st = make(NOW, caller="+19999999999")
    run(t.check_slots())
    assert run(t.book("A", "X", "999-999-9999", "1 Main", "leak", False, True))["ok"]
    res = run(t.send_confirmation_text())
    assert not res["ok"] and "TEST_MODE" in res["error"] or "blocked" in res["error"]


def test_scrub_and_phone():
    assert "4111" not in scrub("card 4111 1111 1111 1111 thanks")
    assert "123-45-6789" not in scrub("ssn 123-45-6789")
    assert normalize_phone("(205) 555-0100") == "+12055550100"
    assert normalize_phone("12") == ""


def test_take_message_and_summary():
    t, _, st = make(NOW)
    run(t.take_message("Bob", "", "existing job, gutters", "urgent"))
    s = owner_summary(CFG.business_name, "+12055550100", st)
    assert "URGENT MESSAGE" in s and "+12055550100" in s


def test_transfer_once():
    tw = FakeTwilio()
    t, _, st = make(NOW, tw=tw)
    assert run(t.transfer("wants a human"))["ok"]
    assert not run(t.transfer("again"))["ok"]
    assert len(tw.transfers) == 1


def test_calendar_down_returns_message_path():
    class Down(FakeCalendar):
        async def busy(self, *a):
            raise RuntimeError("503")
    t, _, st = make(NOW)
    t.cal = Down()
    r = run(t.check_slots())
    assert not r["ok"] and st.errors


def test_prompt_has_disclosure_rules_and_dates():
    p = system_prompt(CFG, NOW, "+12055550100")
    assert "2026-11-26" in p and "Never quote prices" in p and "automated assistant" in p
    assert "automated assistant" in greeting(CFG)
    assert is_open(CFG, NOW)
    assert not is_open(CFG, datetime(2026, 11, 29, 10, 0, tzinfo=CT))  # Sunday


def test_cost_math():
    m = CallMetrics(duration_s=150, prompt_tokens=30000, completion_tokens=600, tts_bytes=900)
    c = m.cost()
    assert 0 < c["total"] < 0.2


def test_owner_summary_has_calendar_block_link():
    t, _, st = make(NOW)
    run(t.check_slots())
    run(t.book("A", "Jane", "2055550100", "1 Main St, Tuscaloosa", "leak", True, True))
    s = owner_summary(CFG.business_name, "+12055550100", st)
    assert "BOOKED" in s and "calendar.google.com/calendar/render" in s


def test_google_busy_merges_roofer_and_bookings_calendars():
    from agent.calendar_backend import GoogleCalendar

    class Q:
        def __init__(self, body):
            self.body = body

        def execute(self):
            return {"calendars": {
                "roofer@x.com": {"busy": [{"start": "2026-11-26T16:00:00Z", "end": "2026-11-26T17:00:00Z"}]},
                "bot@sa.com": {"busy": [{"start": "2026-11-27T15:00:00Z", "end": "2026-11-27T16:00:00Z"}]},
            }}

    class FB:
        def query(self, body):
            assert {i["id"] for i in body["items"]} == {"roofer@x.com", "bot@sa.com"}
            return Q(body)

    class Svc:
        def freebusy(self):
            return FB()

    g = GoogleCalendar.__new__(GoogleCalendar)
    g._svc, g.calendar_id, g.bookings_id = Svc(), "roofer@x.com", "bot@sa.com"
    busy = run(g.busy(NOW, NOW + timedelta(days=3)))
    assert len(busy) == 2
