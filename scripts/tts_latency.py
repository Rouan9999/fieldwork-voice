"""A/B Fish models: time to first audio byte over HTTP streaming, 8 kHz-ready PCM.

    FISH_API_KEY=... python scripts/tts_latency.py [runs]
Saves one sample per model to samples/<model>.wav so Rouan can listen.
Cost: ~5 sentences x runs x 2 models x ~90 bytes = well under $0.05.
"""

import asyncio
import os
import statistics
import sys
import time
import wave
from pathlib import Path

import aiohttp

VOICE = os.getenv("FISH_VOICE_ID", "e3cd384158934cc9a01029cd7d278634")  # Laura
MODELS = os.getenv("FISH_MODELS", "s2.1-pro,drama-3").split(",")
LINES = [
    "Thanks for calling Tuscaloosa Test Roofing. I'm the automated assistant. How can I help you today?",
    "I have Thursday at 10 AM, Thursday at 2 PM, or Friday at 9 AM. Which works best?",
    "Got it. Can I get the address for the inspection, with the city?",
    "Just to confirm, that's Thursday, December 3rd at 10 AM at 4120 Oak Street, Tuscaloosa. Is that right?",
    "You're all set. I just sent you a text with the details.",
]


async def one(session, model, text, speed):
    body = {"text": text, "reference_id": VOICE, "format": "pcm", "sample_rate": 16000,
            "latency": "balanced", "prosody": {"speed": speed}}
    headers = {"Authorization": f"Bearer {os.environ['FISH_API_KEY']}", "model": model}
    t0 = time.monotonic()
    first, audio = None, bytearray()
    async with session.post("https://api.fish.audio/v1/tts", json=body, headers=headers) as r:
        if r.status != 200:
            raise RuntimeError(f"{model} HTTP {r.status}: {(await r.text())[:200]}")
        async for chunk in r.content.iter_any():
            if first is None and chunk:
                first = time.monotonic() - t0
            audio += chunk
    return first, time.monotonic() - t0, bytes(audio)


async def main(runs):
    speed = float(os.getenv("FISH_SPEED", "0.95"))
    Path("samples").mkdir(exist_ok=True)
    async with aiohttp.ClientSession() as s:
        for model in MODELS:
            ttfb, totals, nbytes = [], [], 0
            sample = bytearray()
            for _ in range(runs):
                for line in LINES:
                    try:
                        f, t, a = await one(s, model, line, speed)
                    except Exception as e:
                        print(f"{model}: {e}")
                        break
                    ttfb.append(f)
                    totals.append(t)
                    nbytes += len(line.encode())
                    if len(sample) < 16000 * 2 * 30:
                        sample += a
            if ttfb:
                with wave.open(f"samples/{model}.wav", "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(sample)
                print(f"{model}: first-audio p50 {statistics.median(ttfb):.3f}s max {max(ttfb):.3f}s "
                      f"(n={len(ttfb)}), text bytes {nbytes}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 2))
