"""真实音频驱动真实 bot.py（不需要浏览器、不需要麦克风）。

补的是最后一块拼图：`text_probe.py` 走文本通道，**绕过了 STT 和 VAD**；
`verify_stack.py` 测了 STT/VAD/LLM/TTS，但用的是自建管线，不走传输层。
这里把 WAV 音频通过**真实 WebRTC 音频轨**送进真正跑起来的 `bot.py`，
复现浏览器麦克风输入的完整路径。

延迟以**客户端侧时间戳**为准：
    音频发完(t0) → 收到 user-transcription / bot-llm-text / bot-tts-started
这比读日志更诚实，因为它包含了传输与编解码开销，也就是用户真实的等待。

用法：
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
    """读 WAV → 单声道 16kHz 16bit PCM。"""
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
    """按真实时间把 WAV 送出去，播完接着送静音（让 VAD 能判定「说完」）。"""

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
        self._paused = asyncio.Event()  # 未 set 时只送静音

    def start(self):
        self._paused.set()

    @property
    def audio_end_time(self) -> float:
        return self._t_end

    @property
    def speech_end_time(self) -> float:
        """最后一个有效语音样本发出的时刻 —— 这才是「用户说完」。"""
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
    print(f"  音频: {len(pcm) / (TARGET_SR * 2):.2f}s @ {TARGET_SR}Hz 单声道")

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

    # version 必填：缺了后端会回 error-response「Client version unknown」。
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

    # 等开场白说完再提问，否则会互相打断、测不准
    try:
        await asyncio.wait_for(bot_stopped.wait(), timeout=25)
        print("  开场白已播报完毕，开始送音频")
    except TimeoutError:
        print("  未等到开场白结束，直接送音频")

    # 开场白那一轮的事件不能计入，计时从送音频这一刻重新开始
    hits.clear()
    track.start()
    await asyncio.wait_for(track.audio_done.wait(), timeout=30)
    await asyncio.sleep(wait_seconds)
    await pc.close()
    hits["_t0"] = track.speech_end_time
    return hits, counts


def scan_log() -> dict:
    # 必须读「本次 spawn 的 stdout 日志」而不是 bot-latest.log ——
    # 后者是所有运行共用的固定名，会残留上一次运行的内容，
    # 导致交叉校验拿到别的运行的数据（曾出现过与客户端结果对不上的情况）。
    log = SERVER / "logs" / "audio-probe-server.log"
    if not log.exists():
        return {}
    text = log.read_text(encoding="utf-8", errors="replace")
    return {
        "segments": re.findall(r"\[TURN\] 分段延迟.*", text),
        "transcript": re.findall(r"\[TURN\] 识别文本:.*", text),
        "errors": re.findall(r"\[ERROR\].*", text),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=25.0)
    ap.add_argument("--no-spawn", action="store_true")
    ap.add_argument("--wav", default=None, help="覆盖测试音频路径（默认 verify-input-zh.wav）")
    ap.add_argument("--ref", default=None, help="该音频的标准答案文本；给出则计算字错率")
    args = ap.parse_args()

    wav = Path(args.wav) if args.wav else WAV
    if not wav.exists():
        print(f"缺少测试音频 {wav}，请先运行：cd server && uv run ../verify_stack.py")
        return 1
    globals()["WAV"] = wav

    print("=" * 74)
    print("音频链路探针（真实 bot.py + 真实 WebRTC 音频轨）")
    print("=" * 74)

    proc = None
    if not args.no_spawn:
        print(f"[1/3] 拉起 bot.py（{HOST}:{PORT}）…")
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
            print("  bot.py 未在 180s 内就绪")
            return 1
        print("  服务已就绪")
    else:
        print("[1/3] 跳过拉起")

    try:
        print("[2/3] 建立 WebRTC 会话并送入音频")
        hits, counts = asyncio.run(run_session(args.wait, f"audio-{int(time.time())}"))
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

    print()
    print("[3/3] 结果（客户端侧实测，基准 = 用户说完的那一刻）")
    print("=" * 74)
    t0 = hits.get("_t0", 0.0)
    if not t0:
        print("  音频未发送完成")
        return 1

    rows = (
        ("收到识别文本", "user-transcription"),
        ("LLM 开始生成", "bot-llm-started"),
        ("收到首个答案 token", "bot-llm-text"),
        ("TTS 开始合成", "bot-tts-started"),
        ("机器人开始出声", "bot-started-speaking"),
    )
    for name, key in rows:
        ts = hits.get(key)
        print(f"  {name:<22}{(ts - t0) * 1000:8.0f} ms" if ts else f"  {name:<22}     N/A")
    print("-" * 74)
    print(f"  识别文本 : {hits.get('_transcript', '')!r}")
    print(f"  机器人回答: {hits.get('_answer', '')!r}")
    if args.ref:
        from asr_bench import cer

        print(f"  标准答案 : {args.ref!r}")
        print(f"  字错率   : {cer(args.ref, hits.get('_transcript', '')) * 100:.1f}%")
    print("-" * 74)
    if counts:
        print(f"  收到的 RTVI 消息种类: {len(counts)}（总计 {sum(counts.values())} 条）")

    info = scan_log()
    if info.get("segments"):
        print("-" * 74)
        print("  后端日志交叉校验:")
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

    ok = bool(hits.get("bot-started-speaking") or hits.get("bot-tts-started"))
    print("结论:", "语音链路打通 ✅（说完 → 机器人出声）" if ok else "语音链路未打通 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
