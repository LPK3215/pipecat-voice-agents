"""Pipeline assembly, written from scratch for phase 3.

    transport.in -> STT -> user turn aggregation -> [brain] -> TTS -> transport.out

What is deliberately **absent** here, in one line each:

    tools / knowledge base / memory / orchestration / guards

Those are what phase 2 built on top of a local model. In phase 3 they live in the platform, so the
voice module must not have its own -- two brains in one pipeline is the single most
predictable way to break this design.

The brain slot is the only thing this module contributes that phase 1 did not have.
"""

from __future__ import annotations

import dataclasses
import uuid

from agent_client import BrainClient
from brain import BrainProcessor
from loguru import logger
from observability import setup_logging
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner
from settings import Config, build_stt, build_tts, build_vad, load_config


def _maybe_start_stub(cfg: Config):
    """Offline self-test: run the real pipeline against a stand-in brain.

    Clearly labelled as a stand-in -- it is not the real platform, it only lets you hear the
    whole module (VAD/STT/TTS/turn-taking) work before any platform exists.
    """
    if not cfg.stub_brain:
        return cfg, None
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "probe"))
    from stub_agent import serve

    url, shutdown = serve(port=8799, first_chunk_delay=0.8, chunk_delay=0.03)
    logger.warning(f"[BOOT] BRAIN_STUB=1 -> stand-in brain at {url} (offline self-test only)")
    return dataclasses.replace(
        cfg, brain=dataclasses.replace(cfg.brain, base_url=url, api_key="app-stub-key")
    ), shutdown


async def run_session(cfg: Config, transport, *, session_id: str) -> None:
    stt = build_stt(cfg)
    tts = build_tts(cfg)
    vad = build_vad(cfg)

    # The user side only: turn detection and the transcript are the module's business.
    # There is no assistant aggregator because there is no local LLM to aggregate for --
    # The platform owns the conversation; it hands the module finished sentences to speak.
    context = LLMContext()
    user_aggregator, _unused_assistant = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=vad)
    )

    async with BrainClient(cfg.brain) as client:
        brain = BrainProcessor(client, cfg.filler, speak_min_chars=cfg.voice.speak_min_chars)
        # Live activity log: the platform's process events go straight out to the client.
        client.on_event = brain.emit_event
        user_aggregator.event_handler("on_user_turn_started")(brain.on_user_turn_started)
        user_aggregator.event_handler("on_user_turn_stopped")(brain.on_user_turn_stopped)

        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                user_aggregator,
                brain,
                tts,
                transport.output(),
            ]
        )
        worker = PipelineWorker(
            pipeline,
            name="voice-module",
            params=PipelineParams(enable_metrics=True),
        )
        logger.info(
            f"[PIPELINE] in -> STT({cfg.voice.stt_engine}) -> turn -> "
            f"brain(platform={cfg.brain.base_url}) -> TTS({cfg.voice.tts_engine}) -> out"
        )
        logger.info("[PIPELINE] tools/knowledge/memory: none by design (they live in the platform)")

        runner = WorkerRunner(name="voice-module", handle_sigint=True)
        await runner.add_workers(worker)
        await runner.run()
        logger.info(f"[SESSION {session_id}] finished | turns={brain.turns}")


async def bot(runner_args: RunnerArguments) -> None:
    """Entry point the framework's runner calls."""
    cfg = load_config()
    session_id = uuid.uuid4().hex[:8]
    setup_logging(cfg.logs_dir, session_id)

    if not cfg.brain.api_key and not cfg.stub_brain:
        # Fail fast and loudly: a missing key produces "the bot never answers", which looks
        # exactly like a broken pipeline.
        logger.error("[BOOT] BRAIN_API_KEY is not set -- configure .env (or set BRAIN_STUB=1)")
        raise SystemExit(2)

    logger.info(
        f"[BOOT] session {session_id} | brain={cfg.brain.base_url} | "
        f"streaming={cfg.brain.streaming} | filler={cfg.filler.text!r}"
    )
    cfg, stub_shutdown = _maybe_start_stub(cfg)
    try:
        transport_params = {
            "webrtc": lambda: TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
            ),
        }
        transport = await create_transport(runner_args, transport_params)
        await run_session(cfg, transport, session_id=session_id)
    finally:
        if stub_shutdown is not None:
            stub_shutdown()


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
