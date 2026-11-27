"""Per-call metrics: latency, usage, cost estimate, and the owner summary text.

Cost rates are estimates with sources; update when bills arrive.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

# USD. Source + date in comment. Marked GUESS where not verified.
RATES = {
    "twilio_inbound_min": 0.0085,   # twilio.com/en-us/voice/pricing/us, local receive, Nov 25 2026
    "twilio_stream_min": 0.0,       # GUESS: Media Streams price not found on pricing page; check first bill
    "deepgram_min": 0.0077,         # Nova-3 mono streaming regular price (promo $0.0048), deepgram.com/pricing via 3rd-party captures
    "pipecat_min": 0.01,            # agent-1x active minute, daily.co/pricing/pipecat-cloud
    "fish_per_byte": 15 / 1_000_000,  # s2.1-pro, docs.fish.audio pricing
    "haiku_in_tok": 0.10 / 1_000_000,  # platform.claude.com pricing, prompts <=100K
    "haiku_out_tok": 0.50 / 1_000_000,
    "haiku_cache_read_tok": 0.01 / 1_000_000,
}


@dataclass
class CallMetrics:
    latencies: list = field(default_factory=list)  # seconds, caller stops -> bot audio
    first_speech_latency: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    tts_bytes: int = 0
    duration_s: float = 0.0

    def cost(self) -> dict:
        m = self.duration_s / 60
        parts = {
            "twilio": m * (RATES["twilio_inbound_min"] + RATES["twilio_stream_min"]),
            "deepgram": m * RATES["deepgram_min"],
            "pipecat": m * RATES["pipecat_min"],
            "fish": self.tts_bytes * RATES["fish_per_byte"],
            "llm": (self.prompt_tokens * RATES["haiku_in_tok"]
                    + self.completion_tokens * RATES["haiku_out_tok"]
                    + self.cache_read_tokens * RATES["haiku_cache_read_tok"]),
        }
        total = sum(parts.values())
        return {"total": round(total, 4), "per_min": round(total / m, 4) if m else None,
                **{k: round(v, 5) for k, v in parts.items()}}

    def summary(self) -> dict:
        lat = self.latencies
        return {
            "duration_s": round(self.duration_s, 1),
            "turns": len(lat),
            "latency_p50_s": round(statistics.median(lat), 3) if lat else None,
            "latency_max_s": round(max(lat), 3) if lat else None,
            "first_speech_s": self.first_speech_latency,
            "tokens": {"in": self.prompt_tokens, "out": self.completion_tokens, "cache_read": self.cache_read_tokens},
            "tts_bytes": self.tts_bytes,
            "cost_usd": self.cost(),
        }


def owner_summary(business: str, caller: str, state, transcript_url: str = "") -> str:
    """Short text for the owner. Plain words, under ~4 SMS segments."""
    lines = [f"{business} AI call from {caller or 'unknown number'}."]
    if state.booking:
        b = state.booking
        lines.append(
            f"BOOKED: {b['time']}. {b['name']}, {b['phone']}, {b['address']}. "
            f"Issue: {b['issue']}. Insurance claim: {'yes' if b['insurance_claim'] else 'no'}."
        )
        from datetime import datetime

        from .tools import calendar_link
        lines.append("Block it on your calendar: " + calendar_link(
            f"Roof inspection - {b['name']}", datetime.fromisoformat(b["start"]),
            datetime.fromisoformat(b["end"]), f"{b['phone']} - {b['issue']}", b["address"]))
    for m in state.messages:
        tag = m.get("urgency", "normal").upper()
        lines.append(f"{tag} MESSAGE: {m.get('name') or 'no name'}, {m.get('phone') or ''}: {m.get('reason')}")
    if state.transferred:
        lines.append("Caller was transferred to your cell.")
    if not state.booking and not state.messages:
        lines.append("No booking or message (hang-up, wrong number or spam).")
    if state.errors:
        lines.append("Note: a system step failed, check the log.")
    if transcript_url:
        lines.append(f"Transcript: {transcript_url}")
    return "\n".join(lines)
