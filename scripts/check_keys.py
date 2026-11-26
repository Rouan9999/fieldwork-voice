"""Check every key works WITHOUT printing it. Prints OK / FAIL per service.

Runs in GitHub Actions (secrets as env vars). Cost: $0 (read-only calls).
"""

import json
import os
import sys

import requests

T = 15
results = {}


def check(name, fn):
    try:
        msg = fn()
        results[name] = "OK" + (f" ({msg})" if msg else "")
    except KeyError as e:
        results[name] = f"MISSING secret {e}"
    except Exception as e:
        results[name] = f"FAIL {str(e)[:160]}"


def twilio():
    sid, tok = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    r = requests.get(f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json", auth=(sid, tok), timeout=T)
    r.raise_for_status()
    a = r.json()
    nums = requests.get(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/IncomingPhoneNumbers.json",
                        auth=(sid, tok), timeout=T).json().get("incoming_phone_numbers", [])
    return f"type={a.get('type')} status={a.get('status')} numbers={len(nums)}"


def deepgram():
    r = requests.get("https://api.deepgram.com/v1/projects",
                     headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"}, timeout=T)
    r.raise_for_status()
    return f"projects={len(r.json().get('projects', []))}"


def anthropic():
    r = requests.get("https://api.anthropic.com/v1/models",
                     headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                     timeout=T)
    r.raise_for_status()
    ids = [m["id"] for m in r.json().get("data", [])]
    return "haiku-5-5 available" if any("haiku-5-5" in i for i in ids) else f"haiku-5-5 NOT listed ({len(ids)} models)"


def fish():
    h = {"Authorization": f"Bearer {os.environ['FISH_API_KEY']}"}
    r = requests.get("https://api.fish.audio/wallet/self/api-credit", headers=h, timeout=T)
    if r.status_code == 200:
        return f"api credit={r.json().get('credit')}"
    r.raise_for_status()


def google():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/calendar.events",
                      "https://www.googleapis.com/auth/calendar.freebusy"])
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    cal_id = os.environ.get("CALENDAR_ID")
    if not cal_id:
        return f"key valid; service account = {info.get('client_email')}; CALENDAR_ID not set yet"
    fb = svc.freebusy().query(body={"timeMin": "2026-12-01T00:00:00Z", "timeMax": "2026-12-02T00:00:00Z",
                                    "items": [{"id": cal_id}]}).execute()
    errs = fb["calendars"][cal_id].get("errors")
    if errs:
        raise RuntimeError(f"calendar not shared with {info.get('client_email')}: {errs}")
    return "calendar reachable"


for n, f in [("twilio", twilio), ("deepgram", deepgram), ("anthropic", anthropic), ("fish", fish), ("google", google)]:
    check(n, f)
for k, v in results.items():
    print(f"{k:10s} {v}")
sys.exit(0 if all(v.startswith("OK") for v in results.values()) else 1)
