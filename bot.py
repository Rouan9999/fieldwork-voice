"""Fieldwork Media voice agent: Twilio -> Deepgram -> Claude Haiku 5.5 -> Fish -> Twilio.

Runs on Pipecat Cloud (entry point `bot`) or locally:
    python bot.py --transport twilio
One deployment per client; the client is chosen by CLIENT_CONFIG.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from loguru import logger

from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import EndWorkerFrame, MetricsFrame, TTSSpeakFrame
from pipecat.metrics.metrics import LLMUsageMetricsData, TTSUsageMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.fish.tts import FishAudioTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.workers.runner import WorkerRunner

from agent import voices
from agent.calendar_backend import FakeCalendar, GoogleCalendar, UnavailableCalendar, sa_json_shape
from agent.humanize import Humanizer, add_pauses, direct, direction_for
from agent.config import load_config
from agent.messaging import Twilio
from agent.prompt import greeting, system_prompt
from agent.report import CallMetrics, owner_summary
from agent.tools import SCHEMAS, AgentTools, CallState

load_dotenv(override=True)

# Voice defaults. FISH_VOICE_ID = a Fish voice model id (pick in the Fish app).
# Locked Nov 26: Drama 3 at 0.95 with the calm direction + fillers (agent/voices.py, agent/humanize.py).
FISH_MODEL = os.getenv("FISH_MODEL", voices.MODEL)
FISH_SPEED = float(os.getenv("FISH_SPEED", str(voices.SPEED)))
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-5-5")


class UsageObserver(BaseObserver):
    """Collect token/char usage once per frame (observers see every hop)."""

    def __init__(self, metrics: CallMetrics):
        super().__init__()
        self.m = metrics
        self._seen: set[int] = set()

    async def on_push_frame(self, data: FramePushed):
        f = data.frame
        if not isinstance(f, MetricsFrame) or f.id in self._seen:
            return
        self._seen.add(f.id)
        for d in f.data:
            if isinstance(d, LLMUsageMetricsData):
                self.m.prompt_tokens += d.value.prompt_tokens
                self.m.completion_tokens += d.value.completion_tokens
                self.m.cache_read_tokens += d.value.cache_read_input_tokens or 0
            elif isinstance(d, TTSUsageMetricsData):
                self.m.tts_bytes += d.value  # characters; ~= UTF-8 bytes for English


def make_calendar(cfg):
    if os.getenv("CALENDAR_BACKEND", "google") == "fake":
        logger.warning("Using FakeCalendar (no real bookings)")
        return FakeCalendar()
    try:
        return GoogleCalendar(cfg.calendar_id)
    except Exception as e:  # never let a calendar problem kill the call
        logger.exception(f"calendar init failed; continuing without booking: {e}")
        return UnavailableCalendar(str(e))


REQUIRED_ENV = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "DEEPGRAM_API_KEY", "FISH_API_KEY",
                "ANTHROPIC_API_KEY", "GOOGLE_SA_JSON", "CLIENT_CONFIG", "CALENDAR_ID"]


def log_env_check():
    """Names and shapes only, never values."""
    present = {k: bool(os.getenv(k, "").strip()) for k in REQUIRED_ENV}
    logger.info(f"ENV_CHECK present={present} GOOGLE_SA_JSON[{sa_json_shape(os.getenv('GOOGLE_SA_JSON', ''))}]")


async def run_bot(transport, runner_args: RunnerArguments, call_sid: str, caller: str):
    log_env_check()
    cfg = load_config()
    started = time.monotonic()
    metrics = CallMetrics()
    state = CallState(call_sid=call_sid, caller_number=caller)
    twilio = Twilio()
    tools = AgentTools(cfg, make_calendar(cfg), twilio, state)

    stt = DeepgramSTTService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        settings=DeepgramSTTService.Settings(
            model="nova-3-general", language="en-US", smart_format=True,
            keyterm=["roof", "shingles", "gutters", "hail", "leak", "inspection"],
        ),
    )
    llm = AnthropicLLMService(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        settings=AnthropicLLMService.Settings(
            model=LLM_MODEL,
            system_instruction=system_prompt(cfg, datetime.now(cfg.tz), caller),
            max_tokens=300,
            enable_prompt_caching=True,
        ),
    )
    tts = FishAudioTTSService(
        api_key=os.environ["FISH_API_KEY"],
        settings=FishAudioTTSService.Settings(
            model=FISH_MODEL,
            voice=os.getenv("FISH_VOICE_ID") or voices.voice_id(cfg.voice),
            latency="balanced",
            prosody_speed=FISH_SPEED,
        ),
    )

    # Before each sentence goes to Fish: light filler, short pause, calm direction.
    hz = Humanizer(every=3)
    sentence_no = {"n": 0}

    async def humanize(text: str, _text_type) -> str:
        sentence_no["n"] += 1
        out = hz.add_fillers(text, first_line=sentence_no["n"] == 1)
        out = add_pauses(out, hz.rng)
        tone = direction_for(datetime.now(cfg.tz).hour)
        return direct(out, tone) if FISH_MODEL.startswith(("drama", "s2")) else out

    tts.add_text_transformer(humanize, "*")

    # Register tools -------------------------------------------------------
    async def _wrap(fn, params: FunctionCallParams):
        t0 = time.monotonic()
        try:
            result = await fn(**(params.arguments or {}))
        except TypeError as e:
            result = {"ok": False, "error": f"bad arguments: {e}"}
        except Exception as e:  # never crash the call on a tool error
            logger.exception("tool failed")
            state.errors.append(f"{params.function_name}: {e}")
            result = {"ok": False, "error": "system error; take a message"}
        logger.info(f"TOOL {params.function_name} {json.dumps(params.arguments)} -> {result} "
                    f"({time.monotonic() - t0:.2f}s)")
        await params.result_callback(result)

    handlers = {
        "check_slots": tools.check_slots,
        "book": tools.book,
        "send_confirmation_text": tools.send_confirmation_text,
        "take_message": tools.take_message,
        "transfer": tools.transfer,
    }
    for name, fn in handlers.items():
        llm.register_function(name, lambda p, fn=fn: _wrap(fn, p), timeout_secs=15)

    async def end_call(params: FunctionCallParams):
        await params.result_callback({"ok": True})
        await params.llm.push_frame(EndWorkerFrame(), FrameDirection.UPSTREAM)

    llm.register_function("end_call", end_call)

    schemas = [FunctionSchema(name=s["name"], description=s["description"],
                              properties=s["properties"], required=s["required"]) for s in SCHEMAS]
    schemas.append(FunctionSchema(
        name="end_call",
        description="Hang up after you said goodbye (call finished, wrong number, spam).",
        properties={}, required=[]))
    context = LLMContext(tools=ToolsSchema(standard_tools=schemas))

    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer(), user_idle_timeout=8),
    )

    pipeline = Pipeline([
        transport.input(), stt, user_agg, llm, tts, transport.output(), assistant_agg,
    ])

    latency_obs = UserBotLatencyObserver()
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000, audio_out_sample_rate=8000,
            enable_metrics=True, enable_usage_metrics=True,
            # Barge-in is on by default: caller speech (VAD) interrupts bot audio.
        ),
        observers=[latency_obs, UsageObserver(metrics)],
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    @latency_obs.event_handler("on_latency_measured")
    async def _lat(_obs, seconds):
        metrics.latencies.append(seconds)
        logger.info(f"LATENCY caller-stop -> bot-audio: {seconds:.3f}s")

    idle_count = {"n": 0}

    @user_agg.event_handler("on_user_turn_idle")
    async def _idle(_agg):
        idle_count["n"] += 1
        if idle_count["n"] == 1:
            await worker.queue_frames([TTSSpeakFrame("Are you still there?")])
        elif idle_count["n"] == 2:
            await worker.queue_frames([TTSSpeakFrame(
                "I'm going to hang up for now. Please call back anytime. Goodbye.")])
            await asyncio.sleep(4)
            await worker.queue_frames([EndWorkerFrame()])

    @user_agg.event_handler("on_user_turn_started")
    async def _reset_idle(*_):
        idle_count["n"] = 0

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint, force_gc=True)

    @transport.event_handler("on_client_connected")
    async def _connected(_t, _c):
        # Fixed greeting = no LLM wait on pickup; includes AI disclosure.
        await worker.queue_frames([TTSSpeakFrame(greeting(cfg))])

    @transport.event_handler("on_client_disconnected")
    async def _disconnected(_t, _c):
        await runner.cancel()

    async def max_duration_guard():
        await asyncio.sleep(cfg.max_call_minutes * 60)
        await worker.queue_frames([TTSSpeakFrame(
            "I need to wrap up. Someone from the team will follow up with you. Goodbye.")])
        await asyncio.sleep(5)
        await worker.queue_frames([EndWorkerFrame()])

    guard = asyncio.create_task(max_duration_guard())
    try:
        await runner.add_workers(worker)
        await runner.run()
    finally:
        guard.cancel()
        metrics.duration_s = time.monotonic() - started
        summary = owner_summary(cfg.business_name, caller, state)
        logger.info("CALL_REPORT " + json.dumps({
            "client": cfg.client_id, "call_sid": call_sid, "metrics": metrics.summary(),
            "booking": state.booking, "messages": state.messages,
            "transferred": state.transferred, "errors": state.errors,
        }, default=str))
        logger.info("OWNER_SUMMARY\n" + summary)
        if os.getenv("SEND_OWNER_SUMMARY", "0") == "1" and cfg.owner_cell:
            try:
                await twilio.send_sms(cfg.owner_cell, cfg.twilio_number, summary)
            except Exception as e:
                logger.error(f"owner summary text failed: {e}")


async def bot(runner_args: RunnerArguments):
    """Pipecat Cloud / dev runner entry point."""
    transport = await create_transport(
        runner_args,
        {"twilio": lambda: FastAPIWebsocketParams(audio_in_enabled=True, audio_out_enabled=True)},
    )
    call = runner_args.call_data
    call_sid = (call.call_id if call else "") or ""
    # Twilio Media Streams do not send the caller's number by default; the
    # TwiML passes it as <Parameter name="from" value="{{From}}"/>.
    caller = ((call.body or {}).get("from") if call else "") or (call.from_number if call else "") or ""
    try:
        await run_bot(transport, runner_args, call_sid, caller)
    except Exception:
        logger.exception("run_bot crashed")  # full traceback in Pipecat logs
        raise


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
