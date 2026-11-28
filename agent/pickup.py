"""A short 'line picked up' sound: soft click, then a breath of room tone.
Generated in code (no file, no network), so it plays the instant the call connects."""
import math
import random
import struct

SR = 16000


def pickup_sound(seed: int = 7) -> tuple[bytes, int]:
    rng = random.Random(seed)
    out = []
    # 35 ms click: filtered noise with a fast decay
    prev = 0.0
    for i in range(int(SR * 0.035)):
        env = math.exp(-i / (SR * 0.006))
        prev = 0.6 * prev + 0.4 * rng.uniform(-1, 1)
        out.append(prev * env * 0.35)
    # 300 ms quiet room tone so the line doesn't feel dead before she speaks
    for _ in range(int(SR * 0.30)):
        prev = 0.95 * prev + 0.05 * rng.uniform(-1, 1)
        out.append(prev * 0.01)
    pcm = b"".join(struct.pack("<h", int(max(-1, min(1, x)) * 32767)) for x in out)
    return pcm, SR
