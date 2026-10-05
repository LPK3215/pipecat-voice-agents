"""Probe: the whole chain, without a browser -- and it leaves you an audio file to listen to.

    synthesised speech -> websocket -> VAD/STT -> the platform -> TTS -> websocket -> WAV
                                                              \\
                                               activity events -> websocket -> JSON (text frames)

It answers the two questions that matter here: **how long after the user stops talking does the
bot start speaking?**, and **does the page actually receive the platform's process events?**
(written to `logs/` as audio, and asserted as events, so neither is a matter of opinion).

    uv run python probe/verify_audio_e2e.py                 # uses whatever .env says
    uv run python probe/verify_audio_e2e.py --question "你好"

The module must be running with the websocket entry:
    uv run python server/ws_app.py --host 0.0.0.0 --port 8090
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from settings import load_config
from verify_speech_legs import SENTENCE, synthesize

PASS, FAIL = "[OK]", "[FAIL]"
CHUNK_MS = 20


def to_pcm(wav_path: Path, *, rate: int = 16000) -> bytes:
    """Read the synthesised WAV as 16-bit mono PCM at the pipeline's rate.

    Piper speaks at 22.05 kHz, the pipeline (and whisper, and the VAD) wants 16 kHz, so this
    resamples. `audioop` is deprecated in 3.13 but present in 3.12 and needs no dependency;
    a real deployment would hand the transport its own rate instead of resampling here.
    """
    with wave.open(str(wav_path)) as wav_file:
        src_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        width = wav_file.getsampwidth()
        data = wav_file.readframes(wav_file.getnframes())

    if channels != 1 or width != 2:
        raise SystemExit(f"expected 16-bit mono, got {width * 8}-bit {channels}ch")

    if src_rate != rate:
        import audioop

        data, _ = audioop.ratecv(data, 2, 1, src_rate, rate, None)
    return data


async def run(question_wav: Path, out_wav: Path, *, port: int, wait_after: float) -> dict:
    import json

    import websockets

    pcm = to_pcm(question_wav)
    chunk_bytes = int(16000 * 2 * CHUNK_MS / 1000)
    received = bytearray()
    events: list[dict] = []
    marks: dict[str, float] = {}

    async with websockets.connect(f"ws://127.0.0.1:{port}", max_size=None) as ws:
        await asyncio.sleep(1.5)  # let the pipeline start before feeding audio

        async def collect() -> None:
            try:
                while True:
                    data = await ws.recv()
                    if isinstance(data, str):  # text frame: may be a process event
                        try:
                            note = json.loads(data)
                        except ValueError:
                            continue
                        # The pipeline also sends its own client protocol here (`rtvi-ai`),
                        # which is not this contract -- keep only our own events.
                        if isinstance(note, dict) and "kind" in note:
                            events.append(note)
                        continue
                    if data:
                        if "first_audio" not in marks:
                            marks["first_audio"] = time.perf_counter()
                        received.extend(data)
            except Exception:
                return

        collector = asyncio.create_task(collect())

        # speak at real-time pace: the VAD judges silence durations, so time must be real
        for offset in range(0, len(pcm), chunk_bytes):
            await ws.send(pcm[offset : offset + chunk_bytes])
            await asyncio.sleep(CHUNK_MS / 1000)
        marks["sent"] = time.perf_counter()

        await asyncio.sleep(wait_after)
        collector.cancel()

    if received:
        with wave.open(str(out_wav), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(bytes(received))

    marks["received_bytes"] = float(len(received))
    marks["events"] = events  # type: ignore[assignment]
    return marks


def check_events(events: list[dict]) -> tuple[bool, str]:
    """Does the page get enough to answer "what is it doing?".

    The bar is deliberately about *kinds*, not exact wording: while it is thinking, while it is
    calling a tool, what the tool returned, and where the milliseconds went. A platform that
    sends fewer kinds still works -- but then the page shows less, and that should be visible
    here rather than discovered by a user staring at a silent screen.
    """
    kinds = {str(event.get("kind")) for event in events}
    wanted = {"state", "user", "thinking", "tool", "result", "node", "say", "timing"}
    missing = sorted(wanted - kinds)
    return (not missing), f"kinds={sorted(kinds)}" + (f" missing={missing}" if missing else "")


def wait_for_port(port: int, timeout: float = 240.0) -> bool:
    """Poll until the module is listening.

    A fixed sleep here is a coin flip: measured, 25s was enough on one run and not on the next,
    and the failure surfaces as "connection refused" -- which looks exactly like a wiring bug
    rather than "the machine was busy". Poll instead, and say what happened.
    """
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(1.0)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="end-to-end audio probe (no browser)")
    ap.add_argument("--question", default=SENTENCE)
    ap.add_argument("--url", default=None, help="use a module you started; else it is spawned")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--wait-after", type=float, default=12.0)
    args = ap.parse_args()

    cfg = load_config()
    question_wav = cfg.logs_dir / "probe-question.wav"
    answer_wav = cfg.logs_dir / "probe-e2e-answer.wav"

    print("=" * 72)
    print("voice module / end-to-end audio probe (no browser, real brain from .env)")
    print("=" * 72)
    print(f"  brain: {cfg.brain.base_url} (stand-in={cfg.stub_brain})")

    print(f"  1) synthesise the question: {args.question}")
    print(f"     {synthesize(cfg.voice.piper_voice, question_wav, args.question)}")

    server = None
    log_file = None
    if args.url is None:
        print(f"  2) start the websocket entry on :{args.port}")
        # Keep the module's own output: if startup fails, the reason must be readable here
        # instead of hidden (models load on first use, which can take minutes on a busy box).
        server_log = cfg.logs_dir / "probe-server.log"
        log_file = open(server_log, "w", encoding="utf-8")  # noqa: SIM115 -- closed in `finally`
        server = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parent.parent / "server" / "ws_app.py"),
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        if not wait_for_port(args.port):
            server.terminate()
            print(f"  {FAIL} the module never started listening on :{args.port}")
            print(f"        its output ({server_log}):")
            for line in server_log.read_text(encoding="utf-8").splitlines()[-8:]:
                print(f"          {line}")
            return 1
    else:
        print(f"  2) using the already-running module at {args.url}")

    try:
        print("  3) speak into it and wait for the answer")
        marks = asyncio.run(
            run(question_wav, answer_wav, port=args.port, wait_after=args.wait_after)
        )
    finally:
        if server is not None:
            server.terminate()
        if log_file is not None:
            log_file.close()

    ok = marks.get("received_bytes", 0) > 0
    if ok:
        print(f"  {PASS} the module spoke back: {int(marks['received_bytes'])} bytes of audio")
        print(f"        {answer_wav}")
        print("        (compare with logs/voice-module-*.log for the per-stage breakdown,")
        print("         which separates: heard / platform's first word / first sentence / TTS)")
    else:
        print(f"  {FAIL} nothing came back -- check the module log under logs/")

    events = marks.get("events", [])
    events_ok, detail = check_events(events)
    print("  4) the page's activity log (text frames, separate from the audio)")
    print(f"     {PASS if events_ok else FAIL} {len(events)} event(s): {detail}")
    for event in events[:12]:
        print(f"        - {event.get('kind')}: {event.get('text') or event.get('state') or ''}")
    if len(events) > 12:
        print(f"        … {len(events) - 12} more")

    print("-" * 72)
    verdict = ok and events_ok
    audio_state = "ok" if ok else "missing"
    event_state = "ok" if events_ok else "incomplete"
    print(
        f"verdict: {PASS} audio round trip + activity events both work"
        if verdict
        else f"verdict: {FAIL} audio={audio_state}, events={event_state}"
    )
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
