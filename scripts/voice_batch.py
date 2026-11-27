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
CATHERINE = "7fe8fcea5c3d4fbb9d153493a1e8be0e"  # "Kthy" community voice (rights unclear: test only)

from agent.humanize import Humanizer, direct, strip_cues  # noqa: E402

# name -> provider, model, voice, fish direction prefix?, $ per 1M bytes (list price; measured below for Fish)
VOICES = {
    "drama3_catherine_calm": ("fish", "drama-3", CATHERINE, True, 10.0),
    "drama3_catherine_nodirection": ("fish", "drama-3", CATHERINE, False, 10.0),
    "drama3_laura_calm": ("fish", "drama-3", LAURA, True, 10.0),
    "s21free_catherine_calm": ("fish", "s2.1-pro-free", CATHERINE, True, 0.0),
    "deepgram_arcas_male": ("deepgram", "aura-2-arcas-en", None, False, 30.0),
}
CALLER_VOICE = "aura-2-arcas-en"
CALLER_VOICE_IF_AGENT_IS_ARCAS = "aura-2-apollo-en"

PERSONA = dict(
    persona=("You are Mike, a homeowner in Hoover, Alabama. Last night's hail storm hit your house and you saw "
             "granules in the gutters. You think you might file an insurance claim but you're not sure. You're a bit "
             "unsure how this works and ask one natural question about whether it costs anything. Prefer an afternoon. "
             "Address: 2214 Lorna Ridge Lane, Hoover. Callback: the number you're calling from. Talk casually, "
             "like a real person on the phone. Confirm when the details are read back correctly."),
    opener="Hey, uh, yeah, we had that hail come through last night and I think it might've messed up my roof.",
    expect=dict(booked=True),
)


async def fish_credit(session):
    h = {"Authorization": f"Bearer {os.environ['FISH_API_KEY']}"}
    async with session.get("https://api.fish.audio/wallet/self/api-credit", headers=h) as r:
        return float((await r.json(content_type=None)).get("credit", 0)) if r.status == 200 else None


async def fish(session, model, text, voice=LAURA):
    body = {"text": text, "reference_id": voice, "format": "pcm", "sample_rate": SR,
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

    # Same filler placement for every voice, so only the voice differs.
    hz = Humanizer(every=3, seed=7)
    human = [(w, hz.add_fillers(t, first_line=(i == 0)) if w == "agent" else t) for i, (w, t) in enumerate(dialog)]
    Path("samples/dialog_humanized.json").write_text(json.dumps(human, indent=2))
    for who, line in human:
        if who == "agent":
            print(f"  HUMANIZED {line}")

    only = [v for v in os.getenv("VOICES", "").split(",") if v]
    rows = []
    async with aiohttp.ClientSession() as s:
        caller_cache = {}

        async def caller(i, line, voice):
            if (i, voice) not in caller_cache:
                caller_cache[(i, voice)] = (await deepgram(s, voice, line))[1]
            return caller_cache[(i, voice)]

        for name, (prov, model, voice, use_dir, price) in VOICES.items():
            if only and name not in only:
                continue
            cvoice = CALLER_VOICE_IF_AGENT_IS_ARCAS if model == "aura-2-arcas-en" else CALLER_VOICE
            pcm, firsts, nbytes = bytearray(), [], 0
            before = await fish_credit(s) if prov == "fish" else None
            try:
                for i, (who, line) in enumerate(human):
                    if who == "caller":
                        pcm += await caller(i, line, cvoice) + gap(350)
                        continue
                    if prov == "fish":
                        text = direct(line) if use_dir else line
                        f, a = await fish(s, model, text, voice)
                    else:
                        text = strip_cues(line)
                        f, a = await deepgram(s, model, text)
                    nbytes += len(text.encode())
                    firsts.append(f)
                    pcm += a + gap(450)
            except Exception as e:
                rows.append((name, f"SKIPPED: {e}"))
                print(f"{name}: SKIPPED {e}")
                continue
            after = await fish_credit(s) if prov == "fish" else None
            to_mp3(bytes(pcm), out / f"call_{name}.mp3")
            spent = (before - after) if (before is not None and after is not None) else None
            measured = f", Fish credit used ${spent:.4f} (= ${spent / nbytes * 1e6:.2f} per 1M bytes)" if spent else ""
            rows.append((name, f"first-audio p50 {statistics.median(firsts):.2f}s max {max(firsts):.2f}s, "
                               f"{nbytes} bytes, list cost ${nbytes * price / 1e6:.4f}{measured}"))
            print(f"{name}: {rows[-1][1]}")
    Path("samples/results.txt").write_text("\n".join(f"{n}: {r}" for n, r in rows))


if __name__ == "__main__":
    asyncio.run(main())
