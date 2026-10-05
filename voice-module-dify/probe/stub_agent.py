"""A stand-in brain, for offline self-test only.

This is **not** the real platform (that has to be an actual agent system). It exists so the
module's brain interface can be exercised with no platform running: same request shape, same
SSE chunk shape, one delay knob.

Run it standalone for a manual check:
    uv run python probe/stub_agent.py --port 8799 --first-chunk-delay 0.6
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: something only the brain knows -- the probe uses it to prove the answer came from the platform
INTERNAL_CODE = "VX-42"


def _answer_for(query: str) -> str:
    if "内部代号" in query or "代号" in query:
        return f"我的内部代号是 {INTERNAL_CODE}。"
    return f"你说的是「{query}」。我是替身脑袋，只用于离线自测。"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    first_chunk_delay = 0.3
    chunk_delay = 0.05

    def log_message(self, *_args):  # keep the probe output clean
        pass

    def _json_error(self, code: int, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._json_error(400, "invalid json")

        if not (self.headers.get("Authorization") or "").startswith("Bearer "):
            return self._json_error(401, "invalid API token")

        if self.path.endswith("/stop"):
            return self._json_error(200, "stopped")  # stop is best effort

        if not self.path.rstrip("/").endswith("/chat-messages"):
            return self._json_error(404, "not found")

        query = str(payload.get("query") or "")
        answer = _answer_for(query)
        conversation_id = payload.get("conversation_id") or str(uuid.uuid4())
        task_id = str(uuid.uuid4())
        streaming = payload.get("response_mode") == "streaming"

        if not streaming:
            body = json.dumps(
                {"answer": answer, "conversation_id": conversation_id}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        # A real platform streams its process, not just the answer: what it is thinking, which
        # tool it called, how the workflow nodes went. The stub mirrors that shape, so the
        # module's event channel can be verified with no platform running -- including the part
        # that matters most: the events arrive **during** the wait, not after it.
        self._event(
            {"event": "agent_thought", "thought": "先看用户在问什么"},
            conversation_id,
            task_id,
        )
        time.sleep(self.first_chunk_delay)  # the platform's "thinking" time
        self._event(
            {
                "event": "agent_thought",
                "thought": "要不要查一下知识库",
                "tool": "search_knowledge",
                "tool_input": json.dumps({"query": query[:20]}, ensure_ascii=False),
                "observation": "命中 2 条（替身数据）",
            },
            conversation_id,
            task_id,
        )
        self._event(
            {"event": "node_started", "data": {"title": "检索知识库"}},
            conversation_id,
            task_id,
        )
        self._event(
            {"event": "node_finished", "data": {"title": "检索知识库", "elapsed_time": 0.31}},
            conversation_id,
            task_id,
        )

        for index, piece in enumerate(answer):
            event = {
                "event": "message",
                "answer": piece,
                "conversation_id": conversation_id,
                "task_id": task_id,
            }
            chunk = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
            self._write_chunked(chunk)
            if index < len(answer) - 1:
                time.sleep(self.chunk_delay)
        self._write_chunked(
            f"data: {json.dumps({'event': 'message_end'})}\n\n".encode()
        )
        self._write_chunked(b"")  # terminate the chunked body

    def _event(self, payload: dict, conversation_id: str, task_id: str) -> None:
        payload = {"conversation_id": conversation_id, "task_id": task_id, **payload}
        self._write_chunked(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())

    def _write_chunked(self, payload: bytes) -> None:
        if not payload:
            self.wfile.write(b"0\r\n\r\n")
        else:
            self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
        self.wfile.flush()


def serve(*, port: int = 8799, first_chunk_delay: float = 0.3, chunk_delay: float = 0.05):
    """Start the stub in a background thread; returns (base_url, shutdown_callable)."""
    handler = type(
        "_BoundHandler",
        (_Handler,),
        {"first_chunk_delay": first_chunk_delay, "chunk_delay": chunk_delay},
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def shutdown() -> None:
        server.shutdown()
        server.server_close()

    return f"http://127.0.0.1:{port}/v1", shutdown


def main() -> int:
    ap = argparse.ArgumentParser(description="stand-in brain (offline self-test only)")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--first-chunk-delay", type=float, default=0.6)
    args = ap.parse_args()
    url, _ = serve(port=args.port, first_chunk_delay=args.first_chunk_delay)
    print(f"stub brain listening on {url}/chat-messages  (Ctrl-C to stop)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
