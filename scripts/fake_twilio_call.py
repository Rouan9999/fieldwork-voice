"""Pretend to be Twilio Media Streams: connect, send 'start', stream audio, stop.

Usage: python scripts/fake_twilio_call.py ws://localhost:7860/ws [wav_8k_mono.wav] [seconds]
Without a wav it streams silence (tests greeting + idle hang-up).
Prints when bot audio first arrives (pickup -> greeting latency).
"""

import asyncio
import audioop  # noqa: deprecated but fine on 3.12; falls back below
import base64
import json
import sys
import time
import wave

import websockets


def mulaw_chunks(path: str | None, seconds: float):
    if path:
        with wave.open(path) as w:
            assert w.getframerate() == 8000 and w.getnchannels() == 1, "need 8 kHz mono wav"
            pcm = w.readframes(w.getnframes())
        mu = audioop.lin2ulaw(pcm, w.getsampwidth())
    else:
        mu = b""
    total = int(seconds * 8000)
    mu = mu + b"\xff" * max(0, total - len(mu))  # 0xFF = mu-law silence
    for i in range(0, len(mu), 160):  # 20 ms frames
        yield mu[i:i + 160]


async def main(url, wav=None, seconds=20):
    async with websockets.connect(url) as ws:
        sid = "CAfake0000000000000000000000000000"
        await ws.send(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
        await ws.send(json.dumps({
            "event": "start", "sequenceNumber": "1", "streamSid": "MZfake",
            "start": {"streamSid": "MZfake", "accountSid": "ACfake", "callSid": sid,
                      "tracks": ["inbound"], "customParameters": {"from": "+12055550100"},
                      "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}},
        }))
        t0 = time.monotonic()
        first_audio = None
        got = 0

        async def reader():
            nonlocal first_audio, got
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("event") == "media":
                    got += 1
                    if first_audio is None:
                        first_audio = time.monotonic() - t0
                        print(f"first bot audio after {first_audio:.3f}s")

        rd = asyncio.create_task(reader())
        seq = 2
        for chunk in mulaw_chunks(wav, seconds):
            await ws.send(json.dumps({"event": "media", "streamSid": "MZfake", "sequenceNumber": str(seq),
                                      "media": {"payload": base64.b64encode(chunk).decode()}}))
            seq += 1
            await asyncio.sleep(0.02)
        await ws.send(json.dumps({"event": "stop", "streamSid": "MZfake"}))
        await asyncio.sleep(1)
        rd.cancel()
        print(f"bot audio frames received: {got}")


if __name__ == "__main__":
    a = sys.argv[1:]
    asyncio.run(main(a[0], a[1] if len(a) > 1 and a[1] != "-" else None, float(a[2]) if len(a) > 2 else 20))
