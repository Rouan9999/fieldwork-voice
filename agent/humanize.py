"""Make agent lines sound less scripted before they reach the voice model.

1. add_fillers: Haiku rarely uses "um" even when allowed, so code adds a light
   filler to about one line in three. Never on read-backs (digits, times,
   addresses) or the greeting, never twice in a row.
2. direct: Drama 3 / S2 read a leading [bracket] as a performance direction.
   One fixed direction keeps the tone calm instead of bubbly.
"""

from __future__ import annotations

import random
import re

FILLERS = ["Um, ", "Uh, ", "Okay, um, ", "So, um, ", "Let me see [short pause] "]
DIRECTION = "[calm and relaxed, natural everyday tone like a real receptionist, not cheerful or salesy]"
DIRECTION_SLOW = ("[calm, relaxed, unhurried pace, slightly tentative, thinking as she talks with small natural "
                  "pauses, like a real receptionist on a slow afternoon, not cheerful or salesy]")
_NO_FILLER = re.compile(r"\d|AM\b|PM\b|read (it|everything|that) back|you're all set", re.I)
_STARTS_WITH_FILLER = re.compile(r"^(um|uh|so|okay|ok|let me|hmm|oh|ah|sorry|yes|yeah|no)\b", re.I)


class Humanizer:
    def __init__(self, every: int = 3, seed: int | None = None):
        self.every = every
        self.since = 0
        self.rng = random.Random(seed)

    def add_fillers(self, line: str, first_line: bool = False) -> str:
        if first_line or _NO_FILLER.search(line) or _STARTS_WITH_FILLER.search(line):
            self.since += 1
            return line
        self.since += 1
        if self.since < self.every:
            return line
        self.since = 0
        f = self.rng.choice(FILLERS)
        rest = line if line.startswith("I ") or line.startswith("I'") else line[:1].lower() + line[1:]
        return f + rest


DIRECTION_NIGHT = ("[quiet, calm, low-key late-night tone, unhurried and a little soft, small natural pauses, "
                   "like a real receptionist taking a call after hours, not cheerful]")


def direction_for(hour: int) -> str:
    """Same person, slightly different energy late at night (9 PM-7 AM)."""
    return DIRECTION_NIGHT if hour >= 21 or hour < 7 else DIRECTION_SLOW


def add_pauses(line: str, rng: random.Random, chance: float = 0.5) -> str:
    """Put a [short pause] after the first clause of longer lines (not read-backs)."""
    if _NO_FILLER.search(line) or len(line) < 50 or rng.random() > chance:
        return line
    m = re.search(r"([,.?]) ", line)
    if not m or m.end() > len(line) - 15:
        return line
    return line[:m.end()] + "[short pause] " + line[m.end():]


def direct(line: str, direction: str = DIRECTION) -> str:
    return f"{direction} {line}"


def strip_cues(line: str) -> str:
    return re.sub(r"\[[^\]]{1,120}\]\s*", "", line)
