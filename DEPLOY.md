# Fieldwork voice agent: deploy, test, onboard, offboard

Status (Wed Nov 25, 2026): code written; 16 offline tests pass; local pipeline
starts and reaches Fish and Deepgram (rejected test keys, as expected). NOT yet
tested with real keys, a real phone call, or a cloud deploy.

## Stack
Twilio number -> TwiML Bin `<Connect><Stream>` -> Pipecat Cloud agent
(Deepgram nova-3 -> Claude Haiku 5.5 + 6 tools -> Fish s2.1-pro "Laura") -> Twilio.
If the agent dies, the TwiML falls through to `<Dial>` = roofer's cell.

## One-time accounts (Rouan)
1. Twilio: upgrade the account (trial blocks `<Stream>`), buy one local number (~$1.15/mo).
2. Pipecat Cloud: https://pipecat.daily.co, create org, add card, then set a cap:
   `pipecat cloud spend-limit --help` (suggest $20/mo while testing).
3. Keys: Deepgram, Fish (paid balance, not free plan), Anthropic.
4. Google Cloud: project `fieldwork-voice`, enable Google Calendar API, create a
   service account, make a JSON key. Never email or paste it.

## Per client (target: under 2 hours of work, live in 2 days)
1. Copy `clients/test-roofing.json` to `clients/<client>.json`, fill in name,
   hours, services, area, FAQ, inspection length. No phone numbers or ids in the file.
2. Client shares one Google Calendar with the service account email,
   permission "Make changes to events". Copy its Calendar ID.
3. Twilio: create a subaccount for the client, buy a local number in it.
4. Pipecat Cloud secret set `fw-<client>` with:
   TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN (the subaccount's), DEEPGRAM_API_KEY,
   FISH_API_KEY, ANTHROPIC_API_KEY, GOOGLE_SA_JSON, OWNER_CELL, TWILIO_NUMBER,
   CALENDAR_ID, CLIENT_CONFIG=clients/<client>.json, TEST_MODE, ALLOWED_TEST_NUMBERS,
   SEND_OWNER_SUMMARY.
5. Deploy (cloud build, no Docker on the laptop):
   `pipecat cloud auth login`
   `pipecat cloud deploy fw-<client> --build-dir . --secrets fw-<client> --profile agent-1x --min-agents 0`
6. Twilio number > Voice > "A call comes in" = TwiML Bin from `twiml/connect.xml`
   (fill AGENT_NAME.ORG_NAME and OWNER_CELL). "Primary handler fails" =
   TwiML Bin from `twiml/fallback.xml`.
7. Test calls from Rouan's phone only (TEST_MODE=1). Then the roofer forwards
   missed / after-hours calls to the Twilio number (carrier conditional forwarding).

## Phase 1 test (on Rouan's phone)
- Call the number. Expect the greeting with AI disclosure.
- Talk over the bot mid-sentence: it must stop (barge-in).
- Stay silent 8 s: "Are you still there?"; 8 s more: goodbye + hang-up.
- In Pipecat Cloud logs, find `LATENCY` lines and the `CALL_REPORT` JSON
  (p50/max latency, tokens, TTS chars, cost estimate). Paste the CALL_REPORT
  line (it has no secrets) into the chat.
- Kill test: stop the agent, call again: the roofer's cell (yours) must ring.
- Check: after a normal goodbye the call ends and does NOT ring your cell
  (the `<Dial>` fallback must only fire on failure).

## Offline checks (any machine with keys)
- `python -m pytest -q` : slot math, timezone/DST, no double-booking, test-mode block.
- `python scripts/eval_calls.py` : 20 scripted text calls against Haiku 5.5.
- `python scripts/tts_latency.py` : Fish s2.1-pro vs drama-3 first-audio time + samples.

## Offboarding
1. Client unshares the calendar (or remove the service account from it).
2. Client turns off call forwarding to the Twilio number.
3. `pipecat cloud agent delete fw-<client>`; delete secret set `fw-<client>`.
4. Release the Twilio number; close the subaccount.
5. Delete the client config and any stored transcripts.
6. Email the client confirming all of the above, with the date.
