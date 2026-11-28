#!/usr/bin/env bash
# End-to-end test of the REAL phone bot without a phone:
# fake Twilio stream -> Deepgram STT -> Haiku 5.5 -> Fish Drama 3 -> audio back.
# Caller audio is made with Deepgram TTS at 8 kHz (phone quality).
# Prints the bot's LATENCY lines (caller stops talking -> first bot audio) and CALL_REPORT.
set -uo pipefail
export CALENDAR_BACKEND=fake TEST_MODE=1 CLIENT_CONFIG=${CLIENT_CONFIG:-clients/capstone-demo.json}
mkdir -p e2e
say() {  # $1 text, $2 out.wav
  curl -s -X POST "https://api.deepgram.com/v1/speak?model=aura-2-arcas-en&encoding=linear16&sample_rate=8000&container=wav" \
    -H "Authorization: Token $DEEPGRAM_API_KEY" -H "Content-Type: application/json" \
    --data "{\"text\": \"$1\"}" -o "$2"
}
say "Hey, uh, we had hail come through last night. Can somebody come look at my roof?" e2e/line1.wav
# Line 1, then 7 s of silence for the bot to answer, then line 2.
say "Afternoon works best for me." e2e/line2.wav
uv run python - <<'PY'
import wave
frames = []
for i, f in enumerate(["e2e/line1.wav", "e2e/line2.wav"]):
    w = wave.open(f)
    frames.append(w.readframes(w.getnframes()))
    frames.append(b"\x00\x00" * 8000 * (7 if i == 0 else 8))
out = wave.open("e2e/caller.wav", "wb")
out.setnchannels(1); out.setsampwidth(2); out.setframerate(8000); out.writeframes(b"".join(frames)); out.close()
print("caller audio seconds:", round(sum(len(x) for x in frames) / 16000, 1))
PY
uv run python bot.py --transport twilio > e2e/bot.log 2>&1 &
BOT=$!
sleep 12
uv run --with audioop-lts --with websockets python scripts/fake_twilio_call.py ws://localhost:7860/ws e2e/caller.wav 26
sleep 3
kill $BOT 2>/dev/null; sleep 2
echo "---- bot log highlights ----"
grep -E "LATENCY|TOOL|CALL_REPORT|Traceback|ERROR|STARTUP|GREETING_DONE|still there" e2e/bot.log | cut -c1-400 | head -40
