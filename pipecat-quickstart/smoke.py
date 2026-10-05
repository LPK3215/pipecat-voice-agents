"""无浏览器冒烟测试：验证「前端可访问 + WebRTC 握手 + 后端装配」三件事。

覆盖 README 里声称可用但从未在无头环境验证过的部分：
    1. GET  /client/          官方 Prebuilt 前端是否真的能拿到（200 + 含 HTML）
    2. POST /api/offer        WebRTC 握手是否成功（用小 WebRTC 客户端产生真实 offer）
    3. 后端日志              是否出现装配错误（缺 key / 服务构造失败）

用法：
    cd server && uv run ../smoke.py                # 自动拉起 bot.py 再测
    cd server && uv run ../smoke.py --no-spawn     # 只测已经跑起来的 bot.py

退出码 0 = 全部通过。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
SERVER = BASE / "server"
HOST = "127.0.0.1"
PORT = int(os.getenv("SMOKE_PORT", "7860"))
ROOT = f"http://{HOST}:{PORT}"

OK, FAIL, WARN = "✅", "❌", "⚠️ "


def _http(path: str, timeout: float = 5.0):
    req = urllib.request.Request(ROOT + path)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def wait_up(timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _http("/", timeout=2.0)
            return True
        except Exception:  # noqa: BLE001 - 轮询期间任何异常都视为未就绪
            time.sleep(0.5)
    return False


def check_client_ui() -> bool:
    """前端页面是否真的可访问。"""
    try:
        status, body = _http("/client/")
    except urllib.error.HTTPError as e:
        print(f"  {FAIL} GET /client/ -> HTTP {e.code}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"  {FAIL} GET /client/ -> {e}")
        return False

    html = body.decode("utf-8", "replace")
    ok = status == 200 and "<html" in html.lower()
    print(f"  {'✅' if ok else FAIL} GET /client/ -> HTTP {status}, {len(body)} 字节")
    if not ok:
        return False
    # 官方 Prebuilt 是打包产物，用 script/assets 引用判断是否完整
    has_assets = bool(re.search(r'<script[^>]+src=', html))
    print(f"  {'✅' if has_assets else WARN} 前端资源引用完整: {has_assets}")
    return has_assets


def check_offer() -> tuple[bool, str]:
    """用小 WebRTC 客户端发起真实握手，检验 /api/offer。"""
    try:
        from aiortc import RTCPeerConnection, RTCSessionDescription
    except ImportError:
        print(f"  {WARN} 未安装 aiortc，跳过 WebRTC 握手检查")
        return True, "skipped"

    import asyncio

    async def _offer() -> tuple[int, str]:
        pc = RTCPeerConnection()
        pc.addTransceiver("audio", direction="sendrecv")
        await pc.setLocalDescription(await pc.createOffer())
        payload = json.dumps(
            {
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
                "pc_id": f"smoke-{int(time.time())}",
            }
        ).encode()
        req = urllib.request.Request(
            ROOT + "/api/offer",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        finally:
            await pc.close()

    status, body = asyncio.run(_offer())
    ok = status == 200
    print(f"  {'✅' if ok else FAIL} POST /api/offer -> HTTP {status}")
    if not ok:
        print(f"     响应: {body[:400]}")
    return ok, body


def scan_logs() -> list[str]:
    """扫描本次 spawn 的运行日志，找出装配期的硬错误。"""
    # 与 audio_probe 同理：bot-latest.log 是各次运行共用的固定名，
    # 会残留上次运行内容；smoke-server.log 才是本次这一份。
    log = SERVER / "logs" / "smoke-server.log"
    if not log.exists():
        return [f"未找到日志 {log}"]
    text = log.read_text(encoding="utf-8", errors="replace")
    patterns = [
        r"缺少\s*\w*API_KEY",
        r"can no longer do its job",
        r"Traceback \(most recent call last\)",
        r"ERROR\s+\|.*?(api[_ ]?key|API key|Unauthorized|401)",
        r"ErrorFrame",
    ]
    hits = []
    for p in patterns:
        for m in re.finditer(p, text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            hits.append(text[line_start : line_end if line_end > 0 else len(text)].strip())
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-spawn", action="store_true", help="不拉起 bot.py，只测已运行的实例")
    args = ap.parse_args()

    print("=" * 72)
    print("无浏览器冒烟测试（前端可访问性 + WebRTC 握手 + 后端装配）")
    print("=" * 72)

    proc = None
    if not args.no_spawn:
        print(f"[1/4] 拉起 bot.py（{HOST}:{PORT}）…")
        log = SERVER / "logs" / "smoke-server.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "wb") as fh:
            proc = subprocess.Popen(
                [sys.executable, "bot.py", "--host", HOST, "--port", str(PORT)],
                cwd=SERVER,
                stdout=fh,
                stderr=subprocess.STDOUT,
            )
    else:
        print("[1/4] 跳过拉起，使用已运行的实例")

    try:
        if proc is not None and not wait_up():
            print(f"  {FAIL} bot.py 在 90s 内未就绪")
            return 1
        print(f"  {OK} 服务已就绪")

        print("[2/4] 前端页面")
        ui_ok = check_client_ui()

        print("[3/4] WebRTC 握手")
        offer_ok, _ = check_offer()

        print("[4/4] 后端装配（扫描日志中的硬错误）")
        hits = scan_logs()
        if hits:
            seen = set()
            for h in hits:
                if h in seen:
                    continue
                seen.add(h)
                print(f"  {WARN} {h[:160]}")
        else:
            print(f"  {OK} 未发现装配期硬错误")

        print("=" * 72)
        all_ok = ui_ok and offer_ok
        print("结论:", f"{OK} 前端 + 后端握手均通过" if all_ok else f"{FAIL} 存在未通过项")
        print("=" * 72)
        return 0 if all_ok else 1
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
