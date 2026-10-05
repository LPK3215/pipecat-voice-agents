"""No browser, no microphone: drive the **real bot.py** over RTVI send-text.

Why go through the real pipeline:
    A synthetic minimal pipeline is not a reliable observation point -- the assistant
    aggregator swallows text frames, so "was there a final answer" is hard to judge and is
    easily misread as a model problem. Here a real bot.py is started and RTVI messages are
    sent over the WebRTC data channel, exactly the path the official prebuilt frontend's
    "Send" button uses.

One run verifies three things:
    1. the text channel works (what the frontend sendText() sends is a send-text message)
    2. whether tool calling closes the loop in the real pipeline ([TOOL] -> final answer -> speech)
    3. which RTVI messages the frontend actually receives (captions / metrics / tool calls / errors)

Usage:
    cd server && uv run ../text_probe.py
    cd server && uv run ../text_probe.py --question "今天星期几"
    cd server && uv run ../text_probe.py --no-spawn        # connect to a running bot.py only
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
SERVER = BASE / "server"
HOST = "127.0.0.1"
PORT = int(os.getenv("PROBE_PORT", "7861"))
ROOT = f"http://{HOST}:{PORT}"

# RTVI wire format: {"label": "rtvi-ai", "type": "...", "data": {...}}
LABEL = "rtvi-ai"


def _post_offer(sdp: str, pc_id: str) -> tuple[int, str]:
    """Start the handshake; returns (HTTP status, response body). The body is the answer SDP."""
    payload = json.dumps({"sdp": sdp, "type": "offer", "pc_id": pc_id}).encode()
    req = urllib.request.Request(
        ROOT + "/api/offer", data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f"  HTTP {e.code}: {body[:300]}")
        return e.code, body


def wait_up(timeout: float = 180.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(ROOT + "/", timeout=2.0)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(1.0)
    return False


async def run_session(question: str, wait_seconds: float, pc_id: str) -> Counter:
    from aiortc import RTCPeerConnection, RTCSessionDescription

    seen: Counter = Counter()
    samples: dict[str, str] = {}
    pc = RTCPeerConnection()
    pc.addTransceiver("audio", direction="sendrecv")
    channel = pc.createDataChannel("chat")
    opened = asyncio.Event()

    @channel.on("open")
    def _on_open():
        opened.set()

    @channel.on("message")
    def _on_message(message):
        try:
            msg = json.loads(message)
        except Exception:  # noqa: BLE001
            return
        if isinstance(msg, dict) and msg.get("label") == LABEL:
            mtype = msg.get("type", "?")
            seen[mtype] += 1
            # Keep a copy of the raw text so failures are self-explanatory.
            samples.setdefault(mtype, json.dumps(msg, ensure_ascii=False)[:300])

    await pc.setLocalDescription(await pc.createOffer())
    status, body = _post_offer(pc.localDescription.sdp, pc_id)
    print(f"  POST /api/offer -> HTTP {status}")
    if status != 200:
        await pc.close()
        return seen, samples

    # The answer SDP must be set back, otherwise the connection is never established and the
    # data channel never opens.
    try:
        answer = json.loads(body)
    except Exception:  # noqa: BLE001 - some versions return a bare SDP
        answer = {"sdp": body, "type": "answer"}
    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=answer["sdp"], type=answer.get("type", "answer"))
    )

    try:
        await asyncio.wait_for(opened.wait(), timeout=30)
    except TimeoutError:
        print("  data channel did not open")
        await pc.close()
        return seen, samples

    # 1) announce the client is ready; 2) send a text question (identical to frontend sendText())
    # aiortc's channel.send() is synchronous, not a coroutine.
    # id is required -- without it the backend rejects the whole message (the prebuilt
    # frontend includes it). version is also required: without it the backend replies with
    # an error-response "Client version unknown" (a compatibility hint).
    from pipecat.processors.frameworks.rtvi.models import PROTOCOL_VERSION

    channel.send(
        json.dumps(
            {
                "label": LABEL,
                "type": "client-ready",
                "id": f"{pc_id}-ready",
                "data": {"version": PROTOCOL_VERSION},
            }
        )
    )
    await asyncio.sleep(1.0)
    channel.send(
        json.dumps(
            {
                "label": LABEL,
                "type": "send-text",
                "id": f"{pc_id}-text",
                "data": {
                    "content": question,
                    "options": {"run_immediately": True, "audio_response": True},
                },
            }
        )
    )
    print(f"  sent text question: {question!r}")

    await asyncio.sleep(wait_seconds)
    await pc.close()
    return seen, samples


def scan_log() -> dict:
    """Extract the key evidence for this turn from the bot log."""
    log = SERVER / "logs" / "bot-latest.log"
    if not log.exists():
        return {}
    text = log.read_text(encoding="utf-8", errors="replace")
    return {
        "tool_calls": re.findall(r"\[TOOL\].*", text),
        "errors": re.findall(r"\[ERROR\].*", text),
        "stt_text": re.findall(r"\[TURN\] transcript:.*", text),
        "segments": re.findall(r"\[TURN\] latency.*", text),
        "tts": re.findall(r"\[TURN\] TTS first audio", text),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", default="现在几点了？")
    ap.add_argument("--wait", type=float, default=45.0, help="how many seconds to wait after asking")
    ap.add_argument("--no-spawn", action="store_true")
    args = ap.parse_args()

    print("=" * 74)
    print("Text channel probe (real bot.py + real WebRTC data channel)")
    print("=" * 74)

    proc = None
    if not args.no_spawn:
        print(f"[1/3] spawning bot.py ({HOST}:{PORT}) ...")
        log = SERVER / "logs" / "text-probe-server.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "wb") as fh:
            proc = subprocess.Popen(
                [sys.executable, "bot.py", "--host", HOST, "--port", str(PORT)],
                cwd=SERVER,
                stdout=fh,
                stderr=subprocess.STDOUT,
            )
    else:
        print("[1/3] spawn skipped, using a running instance")

    try:
        if proc is not None and not wait_up():
            print("  bot.py not ready within 180s")
            return 1
        if proc is not None:
            print("  server ready")

        pc_id = f"probe-{int(time.time())}"
        print("[2/3] establishing WebRTC session and sending a text question")
        seen, samples = asyncio.run(run_session(args.question, args.wait, pc_id))
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

    print()
    print("[3/3] results")
    print("=" * 74)
    print("  RTVI messages the frontend received this turn (count):")
    if seen:
        for t, n in seen.most_common():
            print(f"     {t:<34} x{n}")
        for key in ("error", "error-response"):
            if key in samples:
                print(f"     {key} raw: {samples[key]}")
    else:
        print("     (none)")

    info = scan_log()
    print("-" * 74)
    if info.get("tool_calls"):
        for line in dict.fromkeys(info["tool_calls"]):
            print(f"  tool: {line.strip()[:150]}")
    else:
        print("  tool: not called")
    if info.get("errors"):
        for line in dict.fromkeys(info["errors"]):
            print(f"  error: {line.strip()[:150]}")
    if info.get("segments"):
        for line in dict.fromkeys(info["segments"]):
            print(f"  latency: {line.strip()[:150]}")
    if info.get("tts"):
        print(f"  spoke: {len(info['tts'])} time(s)")
    print("=" * 74)

    # Verdict: the text channel works when the backend really processed the question
    # (the log shows TTS speech or a tool call).
    ok = bool(info.get("tts")) or bool(info.get("tool_calls"))
    print("verdict:", "[OK] text channel works" if ok else "[ERR] text channel not working (see log)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
