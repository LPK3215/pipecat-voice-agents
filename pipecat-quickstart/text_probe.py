"""不用浏览器、不用麦克风：通过 RTVI send-text 驱动**真实的 bot.py**。

为什么要走真实管线：
    合成的最小管线观测不可靠 —— assistant 聚合器会吞掉文本帧，
    导致「有没有最终回答」看不准，还容易误判成模型问题。
    这里起真正的 bot.py，用 WebRTC 数据通道发 RTVI 消息，
    和官方 Prebuilt 前端点「发送」走的是同一条路径。

一次运行验证三件事：
    1. 文本通道可用（前端 sendText() 发的就是 send-text 消息）
    2. 真实管线里工具调用是否闭环（[TOOL] → 最终回答 → 出声）
    3. 前端实际会收到哪些 RTVI 消息（字幕 / metrics / 工具调用 / 错误）

用法：
    cd server && uv run ../text_probe.py
    cd server && uv run ../text_probe.py --question "今天星期几"
    cd server && uv run ../text_probe.py --no-spawn        # 只连已运行的 bot.py
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

# RTVI 线上格式：{"label": "rtvi-ai", "type": "...", "data": {...}}
LABEL = "rtvi-ai"


def _post_offer(sdp: str, pc_id: str) -> tuple[int, str]:
    """发起握手，返回 (HTTP 状态, 响应体)。响应体里是 answer SDP。"""
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
            # 留一份原文，便于失败时直接看出原因
            samples.setdefault(mtype, json.dumps(msg, ensure_ascii=False)[:300])

    await pc.setLocalDescription(await pc.createOffer())
    status, body = _post_offer(pc.localDescription.sdp, pc_id)
    print(f"  POST /api/offer -> HTTP {status}")
    if status != 200:
        await pc.close()
        return seen

    # 必须把 answer SDP 装回去，否则连接不会真正建立、数据通道永远打不开
    try:
        answer = json.loads(body)
    except Exception:  # noqa: BLE001 - 有些版本直接返回裸 SDP
        answer = {"sdp": body, "type": "answer"}
    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=answer["sdp"], type=answer.get("type", "answer"))
    )

    try:
        await asyncio.wait_for(opened.wait(), timeout=30)
    except TimeoutError:
        print("  数据通道未打开")
        await pc.close()
        return seen

    # 1) 声明前端就绪；2) 发文本提问（与前端 sendText() 完全一致）
    # aiortc 的 channel.send() 是同步方法，不是协程。
    # id 必填 —— 少了它后端会整条消息校验失败（Prebuilt 前端会带上）。
    channel.send(
        json.dumps(
            {"label": LABEL, "type": "client-ready", "id": f"{pc_id}-ready", "data": {}}
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
    print(f"  已发送文本提问: {question!r}")

    await asyncio.sleep(wait_seconds)
    await pc.close()
    return seen, samples


def scan_log() -> dict:
    """从 bot 日志里提取这一轮的关键证据。"""
    log = SERVER / "logs" / "bot-latest.log"
    if not log.exists():
        return {}
    text = log.read_text(encoding="utf-8", errors="replace")
    return {
        "tool_calls": re.findall(r"\[TOOL\].*", text),
        "errors": re.findall(r"\[ERROR\].*", text),
        "stt_text": re.findall(r"\[TURN\] 识别文本:.*", text),
        "segments": re.findall(r"\[TURN\] 分段延迟.*", text),
        "tts": re.findall(r"\[TURN\] TTS 开始出声", text),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", default="现在几点了？")
    ap.add_argument("--wait", type=float, default=45.0, help="发问后等待多少秒")
    ap.add_argument("--no-spawn", action="store_true")
    args = ap.parse_args()

    print("=" * 74)
    print("文本通道探针（真实 bot.py + 真实 WebRTC 数据通道）")
    print("=" * 74)

    proc = None
    if not args.no_spawn:
        print(f"[1/3] 拉起 bot.py（{HOST}:{PORT}）…")
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
        print("[1/3] 跳过拉起，使用已运行的实例")

    try:
        if proc is not None and not wait_up():
            print("  bot.py 未在 180s 内就绪")
            return 1
        if proc is not None:
            print("  服务已就绪")

        pc_id = f"probe-{int(time.time())}"
        print("[2/3] 建立 WebRTC 会话并发送文本提问")
        seen, samples = asyncio.run(run_session(args.question, args.wait, pc_id))
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

    print()
    print("[3/3] 结果")
    print("=" * 74)
    print("  前端在这一轮里收到的 RTVI 消息（次数）:")
    if seen:
        for t, n in seen.most_common():
            print(f"     {t:<34} x{n}")
        for key in ("error", "error-response"):
            if key in samples:
                print(f"     {key} 原文: {samples[key]}")
    else:
        print("     （无）")

    info = scan_log()
    print("-" * 74)
    if info.get("tool_calls"):
        for line in dict.fromkeys(info["tool_calls"]):
            print(f"  工具: {line.strip()[:150]}")
    else:
        print("  工具: 未调用")
    if info.get("errors"):
        for line in dict.fromkeys(info["errors"]):
            print(f"  错误: {line.strip()[:150]}")
    if info.get("segments"):
        for line in dict.fromkeys(info["segments"]):
            print(f"  延迟: {line.strip()[:150]}")
    if info.get("tts"):
        print(f"  出声: {len(info['tts'])} 次")
    print("=" * 74)

    # 判定：文本通道生效 = 后端确实处理了这次提问（日志里有 TTS 出声或工具调用）
    ok = bool(info.get("tts")) or bool(info.get("tool_calls"))
    print("结论:", "文本通道生效 ✅" if ok else "文本通道未生效 ❌（见日志）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
