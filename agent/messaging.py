"""Texts and call control through Twilio REST. Test mode refuses any number
not on ALLOWED_TEST_NUMBERS, so testing can only ever reach Rouan's phones."""

from __future__ import annotations

import os
from xml.sax.saxutils import escape

import aiohttp
from loguru import logger


class SafetyBlock(Exception):
    pass


def _allowed(number: str) -> bool:
    if os.getenv("TEST_MODE", "1") != "1":
        return True
    allowed = {n.strip() for n in os.getenv("ALLOWED_TEST_NUMBERS", "").split(",") if n.strip()}
    return number in allowed


class Twilio:
    def __init__(self, account_sid: str | None = None, auth_token: str | None = None):
        self.sid = account_sid or os.getenv("TWILIO_ACCOUNT_SID", "")
        self.token = auth_token or os.getenv("TWILIO_AUTH_TOKEN", "")
        self.base = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}"

    async def _post(self, path: str, data: dict) -> dict:
        auth = aiohttp.BasicAuth(self.sid, self.token)
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{self.base}{path}", data=data, auth=auth) as r:
                body = await r.json(content_type=None)
                if r.status >= 300:
                    raise RuntimeError(f"Twilio {r.status}: {body.get('message')}")
                return body

    async def send_sms(self, to: str, from_: str, body: str) -> str:
        if not _allowed(to):
            raise SafetyBlock(f"TEST_MODE: {to} is not on ALLOWED_TEST_NUMBERS")
        res = await self._post("/Messages.json", {"To": to, "From": from_, "Body": body})
        logger.info(f"SMS queued sid={res.get('sid')}")
        return res.get("sid", "")

    async def transfer(self, call_sid: str, to: str, caller_id: str, say: str) -> None:
        """Redirect the live call to the roofer's cell. If nobody answers in
        20 s, apologize and hang up (the owner already has the message)."""
        if not _allowed(to):
            raise SafetyBlock(f"TEST_MODE: {to} is not on ALLOWED_TEST_NUMBERS")
        twiml = (
            "<Response>"
            f"<Say>{escape(say)}</Say>"
            f'<Dial timeout="20" callerId="{escape(caller_id)}">{escape(to)}</Dial>'
            "<Say>Sorry, nobody could pick up. We have your message and will call you back soon. Goodbye.</Say>"
            "</Response>"
        )
        await self._post(f"/Calls/{call_sid}.json", {"Twiml": twiml})
