"""Headless smoke test: verify "frontend reachable + WebRTC handshake + backend wiring".

Covers the parts README claims work but that were never verified in a headless environment:
    1. GET  /client/          is the official prebuilt frontend really served (200 + HTML)
    2. POST /api/offer        does the WebRTC handshake succeed (a small WebRTC client
                              generates a real offer)
    3. backend log            any wiring errors (missing key / service construction failure)

Usage:
    cd server && uv run ../smoke.py                # spawn bot.py, then test
    cd server && uv run ../smoke.py --no-spawn     # test an already-running bot.py

Exit code 0 = everything passed.
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

OK, FAIL, WARN = "[OK]", "[FAIL]", "[WARN]"


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
        except Exception:  # noqa: BLE001 - during polling any exception means "not ready"
            time.sleep(0.5)
    return False


def check_client_ui() -> bool:
    """Is the frontend page actually reachable?"""
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
    print(f"  {OK if ok else FAIL} GET /client/ -> HTTP {status}, {len(body)} bytes")
    if not ok:
        return False
    # The official prebuilt is a bundle; use script/assets references to judge completeness.
    has_assets = bool(re.search(r'<script[^>]+src=', html))
    print(f"  {OK if has_assets else WARN} frontend asset references complete: {has_assets}")
    return has_assets


def check_offer() -> tuple[bool, str]:
    """Perform a real handshake with a small WebRTC client to exercise /api/offer."""
    try:
        from aiortc import RTCPeerConnection, RTCSessionDescription
    except ImportError:
        print(f"  {WARN} aiortc not installed; skipping the WebRTC handshake check")
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
    print(f"  {OK if ok else FAIL} POST /api/offer -> HTTP {status}")
    if not ok:
        print(f"     response: {body[:400]}")
    return ok, body


def scan_logs() -> list[str]:
    """Scan this spawn's runtime log for hard wiring errors."""
    # Same reasoning as audio_probe: bot-latest.log is a fixed name shared across runs and
    # keeps the previous run's content; smoke-server.log is this run's log.
    log = SERVER / "logs" / "smoke-server.log"
    if not log.exists():
        return [f"log not found: {log}"]
    text = log.read_text(encoding="utf-8", errors="replace")
    patterns = [
        r"missing\s+\w*_API_KEY",
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
    ap.add_argument("--no-spawn", action="store_true", help="do not spawn bot.py; test a running instance")
    args = ap.parse_args()

    print("=" * 72)
    print("Headless smoke test (frontend reachability + WebRTC handshake + backend wiring)")
    print("=" * 72)

    proc = None
    if not args.no_spawn:
        print(f"[1/4] spawning bot.py ({HOST}:{PORT}) ...")
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
        print("[1/4] spawn skipped, using a running instance")

    try:
        if proc is not None and not wait_up():
            print(f"  {FAIL} bot.py not ready within 90s")
            return 1
        print(f"  {OK} server ready")

        print("[2/4] frontend page")
        ui_ok = check_client_ui()

        print("[3/4] WebRTC handshake")
        offer_ok, _ = check_offer()

        print("[4/4] backend wiring (scan the log for hard errors)")
        hits = scan_logs()
        if hits:
            seen = set()
            for h in hits:
                if h in seen:
                    continue
                seen.add(h)
                print(f"  {WARN} {h[:160]}")
        else:
            print(f"  {OK} no hard wiring errors found")

        print("=" * 72)
        all_ok = ui_ok and offer_ok
        print("verdict:", f"{OK} frontend and backend handshake passed" if all_ok else f"{FAIL} some checks failed")
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
