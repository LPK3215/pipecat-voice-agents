"""WebSocket entry point: the same module, reached over TCP instead of WebRTC.

Use this instead of `app.py` whenever the module sits behind ordinary HTTP forwarding (a
cloud IDE's port preview, a reverse proxy, a phone platform). WebRTC needs UDP for media and
that does not traverse those paths -- the page loads, the connection never completes, and the
symptom is "no sound at all", which is exactly what happened in this environment.

    uv run python server/ws_app.py --host 0.0.0.0 --port 8090

Client contract: connect to the websocket, send 16-bit mono PCM at the pipeline's rate
(default 16 kHz) in small chunks as binary frames, and read binary audio back the same way.
The pipeline is identical to `app.py` -- only the transport differs.
"""

from __future__ import annotations

import argparse
import uuid

from app import _maybe_start_stub, run_session
from loguru import logger
from observability import setup_logging
from pipecat.transports.websocket.server import (
    SingleClientWebsocketServerParams,
    SingleClientWebsocketServerTransport,
)
from raw_pcm_serializer import RawPCMFrameSerializer
from settings import load_config


def build_transport(host: str, port: int, *, sample_rate: int = 16000):
    params = SingleClientWebsocketServerParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        serializer=RawPCMFrameSerializer(sample_rate=sample_rate),
    )
    return SingleClientWebsocketServerTransport(params, host=host, port=port)


async def main_async(host: str, port: int) -> None:
    cfg = load_config()
    session_id = uuid.uuid4().hex[:8]
    setup_logging(cfg.logs_dir, session_id)

    if not cfg.brain.api_key and not cfg.stub_brain:
        logger.error("[BOOT] BRAIN_API_KEY is not set -- configure .env (or set BRAIN_STUB=1)")
        raise SystemExit(2)

    logger.info(f"[BOOT] websocket entry | {host}:{port} | brain={cfg.brain.base_url}")
    cfg, stub_shutdown = _maybe_start_stub(cfg)
    try:
        transport = build_transport(host, port)
        await run_session(cfg, transport, session_id=session_id)
    finally:
        if stub_shutdown is not None:
            stub_shutdown()


def main() -> int:
    ap = argparse.ArgumentParser(description="voice module over a websocket")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8090)
    args = ap.parse_args()

    import asyncio

    asyncio.run(main_async(args.host, args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
