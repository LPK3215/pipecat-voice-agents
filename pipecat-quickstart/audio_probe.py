"""Drive the real bot.py with real audio (no browser, no microphone).

This closes the last gap: `text_probe.py` uses the text channel and **bypasses STT and
VAD**; `verify_stack.py` tests STT/VAD/LLM/TTS but on a self-built pipeline, not through
the transport. Here WAV audio is sent over a **real WebRTC audio track** into a genuinely
running `bot.py`, reproducing the full browser-microphone path.

Latency is measured from **client-side timestamps**:
    audio finished (t0) -> user-transcription / bot-llm-text / bot-tts-started
That is more honest than reading logs because it includes transport and codec overhead --
i.e. the user's real wait.

Usage:
    cd server && uv run ../audio_probe.py
    cd server && uv run ../audio_probe.py --no-spawn
"""

import argparse
import asyncio
import fractions
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from aiortc.mediastreams import MediaStreamTrack
from av import AudioFrame

BASE = Path(__file__).resolve().parent
SERVER = BASE / "server"
HOST = "127.0.0.1"
PORT = int(os.getenv("PROBE_PORT", "7862"))
ROOT = f"http://{HOST}:{PORT}"
LABEL = "rtvi-ai"
WAV = BASE / "verify-input-zh.wav"

TARGET_SR = 16000
CHUNK_MS = 20


def load_pcm(path: Path, target_sr: int = TARGET_SR) -> bytes:
    """Read WAV -> mono 16kHz 16-bit PCM."""
    import numpy as np
    import wave

    with wave.open(str(path), "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != target_sr:
        idx = np.linspace(0, len(a) - 1, int(round(len(a) * target_sr / sr)))
        a = np.interp(idx, np.arange(len(a)), a)
    return a.astype(np.int16).tobytes()


class WavTrack(MediaStreamTrack):
    """Send the WAV in real time, then keep sending silence (so VAD can detect end of speech)."""

    kind = "audio"

    def __init__(self, pcm: bytes, tail_silence_s: float = 1.8):
        super().__init__()
        self._samples = int(TARGET_SR * CHUNK_MS / 1000)  # 320 samples = 20ms
        self._chunk_bytes = self._samples * 2
        self._data = pcm + b"\x00" * (int(TARGET_SR * tail_silence_s) * 2)
        self._speech_bytes = len(pcm)
        self._pos = 0
        self._pts = 0
        self.audio_done = asyncio.Event()
        self._t_end = 0.0
        self._t_speech_end = 0.0
        self._paused = asyncio.Event()  # while unset, only silence is sent

    def start(self):
        self._paused.set()

    @property
    def audio_end_time(self) -> float:
        return self._t_end

    @property
    def speech_end_time(self) -> float:
        """When the last valid speech sample was sent -- this is the true "user finished"."""
        return self._t_speech_end

    async def recv(self):
        frame = AudioFrame(format="s16", layout="mono", samples=self._samples)
        if self._paused.is_set() and self._pos < len(self._data):
            data = self._data[self._pos : self._pos + self._chunk_bytes]
            self._pos += self._chunk_bytes
            if not self._t_speech_end and self._pos >= self._speech_bytes:
                self._t_speech_end = time.time()
            if self._pos >= len(self._data):
                self._t_end = time.time()
                self.audio_done.set()
        else:
            data = b"\x00" * self._chunk_bytes
        frame.planes[0].update(data)
        frame.sample_rate = TARGET_SR
        frame.time_base = fractions.Fraction(1, TARGET_SR)
        frame.pts = self._pts
        self._pts += self._samples
        await asyncio.sleep(CHUNK_MS / 1000 * 0.9)
        return frame


def _post_offer(sdp: str, pc_id: str) -> tuple[int, str]:
    payload = json.dumps({"sdp": sdp, "type": "offer", "pc_id": pc_id}).encode()
    req = urllib.request.Request(
        ROOT + "/api/offer", data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
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


async def run_session(wait_seconds: float, pc_id: str) -> tuple[dict, dict]:
    from aiortc import RTCPeerConnection, RTCSessionDescription

    pcm = load_pcm(WAV)
    print(f"  audio: {len(pcm) / (TARGET_SR * 2):.2f}s @ {TARGET_SR}Hz mono")

    track = WavTrack(pcm)
    hits: dict = {}
    counts: dict = {}
    bot_stopped = asyncio.Event()

    pc = RTCPeerConnection()
    pc.addTrack(track)
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
        if not (isinstance(msg, dict) and msg.get("label") == LABEL):
            return
        mtype = msg.get("type", "?")
        counts[mtype] = counts.get(mtype, 0) + 1
        if mtype == "bot-stopped-speaking":
            bot_stopped.set()
        if mtype in (
            "user-transcription",
            "bot-llm-text",
            "bot-llm-started",
            "bot-tts-started",
            "bot-started-speaking",
            "bot-tts-text",
        ):
            hits.setdefault(mtype, time.time())
            if mtype == "user-transcription":
                hits["_transcript"] = msg.get("data", {}).get("text", "")
            if mtype == "bot-tts-text":
                hits.setdefault("_answer", msg.get("data", {}).get("text", ""))

    await pc.setLocalDescription(await pc.createOffer())
    status, body = _post_offer(pc.localDescription.sdp, pc_id)
    print(f"  POST /api/offer -> HTTP {status}")
    if status != 200:
        await pc.close()
        return hits, counts

    try:
        answer = json.loads(body)
    except Exception:  # noqa: BLE001
        answer = {"sdp": body, "type": "answer"}
    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=answer["sdp"], type=answer.get("type", "answer"))
    )
    await asyncio.wait_for(opened.wait(), timeout=30)

    # version is required: without it the backend replies with an error-response
    # "Client version unknown".
    from pipecat.processors.frameworks.rtvi.models import PROTOCOL_VERSION

    channel.send(
        json.dumps(
            {
                "label": LABEL,
                "type": "client-ready",
                "id": f"{pc_id}-r",
                "data": {"version": PROTOCOL_VERSION},
            }
        )
    )

    # Wait for the opening message to finish before asking, otherwise the two interrupt
    # each other and the measurement is useless.
    try:
        await asyncio.wait_for(bot_stopped.wait(), timeout=25)
        print("  opening message finished; starting audio")
    except TimeoutError:
        print("  opening message did not finish; sending audio anyway")

    # The opening-message turn must not be counted; restart the clock when audio starts.
    hits.clear()
    track.start()
    await asyncio.wait_for(track.audio_done.wait(), timeout=30)
    await asyncio.sleep(wait_seconds)
    await pc.close()
    hits["_t0"] = track.speech_end_time
    return hits, counts


def scan_log() -> dict:
    # Must read the stdout log of **this spawn**, not bot-latest.log -- the latter is a
    # fixed name shared by all runs and keeps leftovers from the previous run, which makes
    # the cross-check report another run's data (this happened before).
    log = SERVER / "logs" / "audio-probe-server.log"
    if not log.exists():
        return {}
    text = log.read_text(encoding="utf-8", errors="replace")
    return {
        "segments": re.findall(r"\[TURN\] latency.*", text),
        "transcript": re.findall(r"\[TURN\] transcript:.*", text),
        "errors": re.findall(r"\[ERROR\].*", text),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=25.0)
    ap.add_argument("--no-spawn", action="store_true")
    ap.add_argument("--wav", default=None, help="override the test audio path (default verify-input-zh.wav)")
    ap.add_argument("--ref", default=None, help="reference transcript for this audio; computes CER when given")
    args = ap.parse_args()

    wav = Path(args.wav) if args.wav else WAV
    if not wav.exists():
        print(f"missing test audio {wav}; run first: cd server && uv run ../verify_stack.py")
        return 1
    globals()["WAV"] = wav

    print("=" * 74)
    print("Audio pipeline probe (real bot.py + real WebRTC audio track)")
    print("=" * 74)

    proc = None
    if not args.no_spawn:
        print(f"[1/3] spawning bot.py ({HOST}:{PORT}) ...")
        log = SERVER / "logs" / "audio-probe-server.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "wb") as fh:
            proc = subprocess.Popen(
                [sys.executable, "bot.py", "--host", HOST, "--port", str(PORT)],
                cwd=SERVER,
                stdout=fh,
                stderr=subprocess.STDOUT,
            )
        if not wait_up():
            print("  bot.py not ready within 180s")
            return 1
        print("  server ready")
    else:
        print("[1/3] spawn skipped")

    try:
        print("[2/3] establishing WebRTC session and sending audio")
        hits, counts = asyncio.run(run_session(args.wait, f"audio-{int(time.time())}"))
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

    print()
    print("[3/3] results (client-side measurements, baseline = the moment the user finished)")
    print("=" * 74)
    t0 = hits.get("_t0", 0.0)
    if not t0:
        print("  audio not fully sent")
        return 1

    rows = (
        ("user transcript", "user-transcription"),
        ("LLM started", "bot-llm-started"),
        ("first answer token", "bot-llm-text"),
        ("TTS started", "bot-tts-started"),
        ("bot speaking", "bot-started-speaking"),
    )
    for name, key in rows:
        ts = hits.get(key)
        print(f"  {name:<22}{(ts - t0) * 1000:8.0f} ms" if ts else f"  {name:<22}     N/A")
    print("-" * 74)
    print(f"  transcript : {hits.get('_transcript', '')!r}")
    print(f"  bot answer : {hits.get('_answer', '')!r}")
    if args.ref:
        from asr_bench import cer

        print(f"  reference  : {args.ref!r}")
        print(f"  CER        : {cer(args.ref, hits.get('_transcript', '')) * 100:.1f}%")
    print("-" * 74)
    if counts:
        print(f"  RTVI message kinds received: {len(counts)} (total {sum(counts.values())})")

    info = scan_log()
    if info.get("segments"):
        print("-" * 74)
        print("  backend log cross-check:")
        for line in dict.fromkeys(info["segments"]):
            print(f"    {line.strip()[:160]}")
    if info.get("transcript"):
        for line in dict.fromkeys(info["transcript"]):
            print(f"    {line.strip()[:160]}")
    if info.get("errors"):
        print("-" * 74)
        for line in dict.fromkeys(info["errors"]):
            print(f"    {line.strip()[:160]}")
    print("=" * 74)

    # Four **mutually exclusive** verdicts (consistent with verify_tools.py / HANDBOOK-02
    # section 6): all look like "the bot did not answer well", but the debugging direction
    # differs completely, so they must not be lumped together.
    errors = info.get("errors") or []
    transcript = hits.get("_transcript", "")
    spoke = bool(hits.get("bot-started-speaking") or hits.get("bot-tts-started"))
    if spoke:
        kind = "ok"
    elif errors:
        kind = "failed"
    elif not transcript:
        kind = "no_stt"
    else:
        kind = "no_speech"
    labels = {
        "ok": "[OK] voice path works (user finished -> bot speaks)",
        "failed": "[ERR] call failed (log has [ERROR]) -> check env/config/quota",
        "no_stt": "[ERR] no transcript -> check STT/VAD or whether audio arrived",
        "no_speech": "[ERR] transcript present but no speech -> check LLM/TTS/pipeline",
    }
    print("verdict:", labels[kind])
    return 0 if kind == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
