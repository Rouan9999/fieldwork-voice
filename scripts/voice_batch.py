"""Voice batch: one realistic simulated call, rendered in several voices.

1. Runs a full call with the real prompt + tools (Claude Haiku 5.5 plays both
   the agent and a homeowner caller) for the client in CLIENT_CONFIG.
2. Renders every agent line in each voice below; the caller is always a
   Deepgram male voice so only the agent voice changes.
3. Writes samples/call_<voice>.mp3, samples/dialog.json and a results table:
   first-audio latency per line, characters spoken, voice cost for the call.

    CLIENT_CONFIG=clients/capstone-demo.json python scripts/voice_batch.py
Voices that fail (no credit, bad key) are reported and skipped.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("CLIENT_CONFIG", "clients/capstone-demo.json")

import aiohttp  # noqa: E402

import eval_calls  # noqa: E402  (same folder)

SR = 16000
LAURA = "e3cd384158934cc9a01029cd7d278634"
CUES = re.compile(r"\[[^\]]{1,40}\]\s*")

# name -> (provider, model/voice, keeps [cues]?, $ per 1M chars)  prices: see doc 35
VOICES = {
    "fish_s21_free_laura": ("fish", "s2.1-pro-free", True, 0.0),
    "fish_s21_pro_laura": ("fish", "s2.1-pro", True, 15.0),
    "fish_drama3_laura": ("fish", "drama-3", True, 50.0),
    "deepgram_aura2_thalia": ("deepgram", "aura-2-thalia-en", False, 30.0),
}
CALLER_VOICE = "aura-2-arcas-en"

PERSONA = dict(
    persona=("You are Mike, a homeowner in Hoover, Alabama. Last night's hail storm hit your house and you saw "
             "granules in the gutters. You think you might file an insurance claim but you're not sure. You're a bit "
             "unsure how this works and ask one natural question about whether it costs anything. Prefer an afternoon. "
             "Address: 2214 Lorna Ridge Lane, Hoover. Callback: the number you're calling from. Talk casually, "
             "like a real person on the phone. Confirm when the details are read back correctly."),
    opener="Hey, uh, yeah, we had that hail come through last night and I think it might've messed up my roof.",
    expect=dict(booked=True),
)


async def fish(session, model, text):
    body = {"text": text, "reference_id": LAURA, "format": "pcm", "sample_rate": SR,
            "latency": "balanced", "prosody": {"speed": float(os.getenv("FISH_SPEED", "0.95"))}}
    h = {"Authorization": f"Bearer {os.environ['FISH_API_KEY']}", "model": model}
    return await _stream(session, "https://api.fish.audio/v1/tts", h, body)


async def deepgram(session, voice, text):
    url = f"https://api.deepgram.com/v1/speak?model={voice}&encoding=linear16&sample_rate={SR}&container=none"
    h = {"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}", "Content-Type": "application/json"}
    return await _stream(session, url, h, {"text": text})


async def _stream(session, url, headers, body):
    t0 = time.monotonic()
    first, audio = None, bytearray()
    async with session.post(url, json=body, headers=headers) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}: {(await r.text())[:160]}")
        async for chunk in r.content.iter_any():
            if first is None and chunk:
                first = time.monotonic() - t0
            audio += chunk
    return first, bytes(audio)


def gap(ms):
    return b"\x00\x00" * int(SR * ms / 1000)


def to_mp3(pcm: bytes, out: Path):
    raw = out.with_suffix(".pcm")
    raw.write_bytes(pcm)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1",
                    "-i", str(raw), "-b:a", "64k", str(out)], check=True)
    raw.unlink()


async def main():
    out = Path("samples")
    out.mkdir(exist_ok=True)
    call = await eval_calls.run_call("voice_batch_call", PERSONA)
    dialog = call["dialog"]
    Path("samples/dialog.json").write_text(json.dumps({"booking": call["booking"], "dialog": dialog}, indent=2))
    print("CALL booked:", bool(call["booking"]), "| LLM cost $", call["llm_cost_usd"],
          "| avg LLM s", call["llm_latency_avg_s"])
    for who, line in dialog:
        print(f"  {who.upper():6s} {line}")

    only = [v for v in os.getenv("VOICES", "").split(",") if v]
    rows = []
    async with aiohttp.ClientSession() as s:
        caller_audio = {}
        for i, (who, line) in enumerate(dialog):
            if who == "caller":
                caller_audio[i] = (await deepgram(s, CALLER_VOICE, line))[1]
        for name, (prov, model, keeps_cues, price) in VOICES.items():
            if only and name not in only:
                continue
            pcm, firsts, chars = bytearray(), [], 0
            try:
                for i, (who, line) in enumerate(dialog):
                    if who == "caller":
                        pcm += caller_audio[i] + gap(350)
                        continue
                    text = line if keeps_cues else CUES.sub("", line)
                    chars += len(text.encode())
                    f, a = await (fish(s, model, text) if prov == "fish" else deepgram(s, model, text))
                    firsts.append(f)
                    pcm += a + gap(450)
            except Exception as e:
                rows.append((name, f"SKIPPED: {e}"))
                print(f"{name}: SKIPPED {e}")
                continue
            to_mp3(bytes(pcm), out / f"call_{name}.mp3")
            cost = chars * price / 1_000_000
            rows.append((name, f"first-audio p50 {statistics.median(firsts):.2f}s max {max(firsts):.2f}s, "
                               f"{chars} chars, voice cost this call ${cost:.4f}"))
            print(f"{name}: {rows[-1][1]}")
    Path("samples/results.txt").write_text("\n".join(f"{n}: {r}" for n, r in rows))


if __name__ == "__main__":
    asyncio.run(main())
