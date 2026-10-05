"""Client for the platform -- the external agent system that owns the thinking.

The whole point of phase 3: the brain is a **network service**, so this module only has
to speak its protocol. Two calls matter:

    POST {base}/chat-messages             -- ask, and read the answer as it is generated
    POST {base}/chat-messages/{task}/stop -- "stop talking", fired when the user barges in

The shapes here are the platform's documented Service API (verified against its source:
`query` / `user` / `conversation_id` / `response_mode`, SSE chunks carrying an `answer`
field). Another platform with the same shape drops in by changing BRAIN_BASE_URL.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
from settings import BrainConfig


class BrainError(RuntimeError):
    """The platform failed to answer. Carries a line the assistant may say out loud."""

    def __init__(self, message: str, *, spoken: str = "抱歉，我这边查东西出错了。"):
        super().__init__(message)
        self.spoken = spoken


@dataclass
class BrainTurn:
    """One request/answer exchange, with the handles needed to interrupt it."""

    conversation_id: str | None = None
    task_id: str | None = None
    text: str = ""
    #: every chunk that arrived, in order (probes and logs read this)
    chunks: list[str] = field(default_factory=list)


class BrainClient:
    """Streaming client for the platform. Failure is always visible: it raises `BrainError`, never
    returns an empty answer that looks like the user said nothing."""

    def __init__(self, cfg: BrainConfig, *, client: httpx.AsyncClient | None = None):
        self._cfg = cfg
        self._client = client
        self._owns_client = client is None
        #: the exchange currently in flight (or the last one). `task_id` becomes readable as
        #: soon as the first event arrives, which is what makes barge-in possible.
        self.last_turn = BrainTurn()

    async def __aenter__(self) -> BrainClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---------------------------------------------------------------- headers

    def _headers(self) -> dict[str, str]:
        if not self._cfg.api_key:
            raise BrainError(
                "BRAIN_API_KEY is not set",
                spoken="我还没配置好连接，请联系管理员。",
            )
        return {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

    # ---------------------------------------------------------------- ask

    async def stream(self, text: str, *, conversation_id: str | None = None) -> AsyncIterator[str]:
        """Yield the answer chunk by chunk. Streaming is not optional for voice: the module
        starts speaking on the first sentence, so the first chunk *is* the latency budget."""
        assert self._client is not None, "use BrainClient as an async context manager"
        payload: dict = {
            "query": text,
            "inputs": {},
            "user": self._cfg.user,
            "response_mode": "streaming" if self._cfg.streaming else "blocking",
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id

        turn = BrainTurn(conversation_id=conversation_id)
        self.last_turn = turn
        try:
            async with self._client.stream(
                "POST", self._cfg.chat_url, headers=self._headers(), json=payload
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")[:300]
                    raise BrainError(
                        f"brain returned HTTP {resp.status_code}: {body}",
                        spoken="抱歉，我这边连不上，请稍后再试。",
                    )
                if not self._cfg.streaming:
                    data = json.loads((await resp.aread()).decode("utf-8", "replace"))
                    turn.text = str(data.get("answer", ""))
                    turn.conversation_id = data.get("conversation_id") or turn.conversation_id
                    if turn.text:
                        yield turn.text
                    return

                async for chunk in self._iter_sse(resp, turn):
                    yield chunk
        except httpx.HTTPError as exc:
            # Network-level failure. Must not look like "the user said nothing".
            raise BrainError(f"brain unreachable: {type(exc).__name__}: {exc}") from exc

    async def _iter_sse(self, resp: httpx.Response, turn: BrainTurn) -> AsyncIterator[str]:
        """Parse `text/event-stream` frames and yield the answer deltas."""
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue  # keep-alive or comment line
            kind = event.get("event")
            if event.get("conversation_id"):
                turn.conversation_id = event["conversation_id"]
            if event.get("task_id"):
                turn.task_id = event["task_id"]
            if kind in ("message", "agent_message"):
                delta = str(event.get("answer") or "")
                if delta:
                    turn.chunks.append(delta)
                    turn.text += delta
                    yield delta
            elif kind == "error":
                raise BrainError(f"brain stream error: {event}")

    # ---------------------------------------------------------------- stop

    async def stop(self, task_id: str) -> bool:
        """Tell the platform to stop generating (the user barged in). Best effort by design:
        a failure here must not break the call, so it returns False instead of raising."""
        assert self._client is not None
        try:
            resp = await self._client.post(
                self._cfg.stop_url(task_id),
                headers=self._headers(),
                json={"user": self._cfg.user},
            )
            return resp.status_code < 400
        except httpx.HTTPError:
            return False
