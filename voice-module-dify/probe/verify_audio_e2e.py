"""Probe: the whole chain, without a browser -- and it leaves you an audio file to listen to.

    synthesised speech -> websocket -> VAD/STT -> the platform -> TTS -> websocket -> WAV

It answers the only question that matters for voice: **how long after the user stops talking
does the bot start speaking?** and writes the returned audio to `logs/` so the answer can be
heard, not just counted.

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
    import websockets

    pcm = to_pcm(question_wav)
    chunk_bytes = int(16000 * 2 * CHUNK_MS / 1000)
    received = bytearray()
    marks: dict[str, float] = {}

    async with websockets.connect(f"ws://127.0.0.1:{port}", max_size=None) as ws:
        await asyncio.sleep(1.5)  # let the pipeline start before feeding audio

        async def collect() -> None:
            try:
                while True:
                    data = await ws.recv()
                    if isinstance(data, bytes) and data:
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
    return marks


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
    if args.url is None:
        print(f"  2) start the websocket entry on :{args.port}")
        server = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parent.parent / "server" / "ws_app.py"),
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(25)  # models load on first use
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

    ok = marks.get("received_bytes", 0) > 0
    if ok:
        print(f"  {PASS} the module spoke back: {int(marks['received_bytes'])} bytes of audio")
        print(f"        {answer_wav}")
        print("        (compare with logs/voice-module-*.log for the per-stage breakdown,")
        print("         which separates: heard / platform's first word / first sentence / TTS)")
    else:
        print(f"  {FAIL} nothing came back -- check the module log under logs/")

    print("-" * 72)
    print(
        f"verdict: {PASS} audio round trip works (listen to {answer_wav.name})"
        if ok
        else f"verdict: {FAIL} no audio returned"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
