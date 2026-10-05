"""The adapter: where the voice module (ears and mouth) meets the platform (the brain).

Duties, in order of how easy they are to get wrong:

1. **Turn detection stays here.** The platform never hears audio. We wait for the framework's
   "user finished talking" event, take the transcript, and only then ask the platform.
2. **Speak on the first sentence, not the full answer.** The platform is a network
   round-trip; waiting for it to finish would add its whole generation time to
   "how long until the bot talks".
   So we accumulate the platform's chunks, and hand each finished sentence to TTS immediately.
3. **Fill the silence.** If the platform is slow the user must hear something
   ("嗯，我看一下。"), or a working system feels broken.
4. **Interrupt properly.** When the user barges in: stop talking locally, cancel the stream,
   and tell the platform to stop generating.
"""

from __future__ import annotations

import asyncio
import re

from agent_client import BrainClient, BrainError
from loguru import logger
from observability import TurnTimeline
from pipecat.frames.frames import Frame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from settings import FillerConfig

#: A sentence ends at one of these, or when the buffer grows past the cap.
_SENTENCE_END = re.compile(r"[。！？!?；;\n]|\.\s")
_MAX_BUFFER = 60


class BrainProcessor(FrameProcessor):
    """Sits between the user aggregator and TTS. Frames pass through untouched; the talking
    happens in the turn handlers wired up by `app.py`."""

    def __init__(self, client: BrainClient, filler: FillerConfig, *, name: str = "brain"):
        super().__init__(name=name)
        self._client = client
        self._filler = filler
        #: The platform's conversation handle: keeping it makes it remember earlier turns.
        self.conversation_id: str | None = None
        self._task: asyncio.Task | None = None
        self._filler_task: asyncio.Task | None = None
        self._timeline: TurnTimeline | None = None
        self._speaking = False
        self.turns = 0

    # ---------------------------------------------------------------- pipeline

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        # Transparent: this processor never consumes frames, it only adds speech.
        await self.push_frame(frame, direction)

    # ---------------------------------------------------------------- turn events

    async def on_user_turn_started(self, *_args) -> None:
        """Barge-in: the user started talking while the platform was still answering."""
        if self._speaking:
            logger.info("[BRAIN] user interrupted -- stopping the platform and local speech")
            await self._cancel_current()
            await self.push_frame(TTSSpeakFrame("", append_to_context=False))

    async def on_user_turn_stopped(self, _aggregator, _strategy, message=None, **_kwargs) -> None:
        """The user finished a turn: ask the platform and start speaking its answer."""
        text = ""
        if message is not None:
            text = getattr(message, "content", None) or getattr(message, "text", "") or ""
        text = str(text).strip()
        if not text:
            logger.info("[BRAIN] empty transcript -- nothing to ask")
            return

        self.turns += 1
        self._timeline = TurnTimeline()
        logger.info(f"[TURN {self.turns}] user: {text}")
        await self._cancel_current()
        self._task = asyncio.create_task(self._answer(text))

    # ---------------------------------------------------------------- the answer

    async def _answer(self, text: str) -> None:
        timeline = self._timeline
        buffer = ""
        spoke_anything = False
        self._speaking = True
        try:
            if self._filler.text and self._filler.delay_secs > 0:
                self._filler_task = asyncio.create_task(self._speak_filler_later())
            assert timeline is not None
            timeline.mark("brain_request")
            async for delta in self._client.stream(text, conversation_id=self.conversation_id):
                if timeline is None:  # cancelled mid-flight
                    return
                if "brain_first_chunk" not in timeline.marks:
                    timeline.mark("brain_first_chunk")
                    if self._filler_task is not None:
                        self._filler_task.cancel()
                        self._filler_task = None
                buffer += delta
                for sentence in self._take_sentences(buffer, last=False):
                    buffer = buffer[len(sentence) :]
                    await self._speak(sentence, timeline, first=not spoke_anything)
                    spoke_anything = True

            # flush whatever is left (the platform often ends without punctuation)
            for sentence in self._take_sentences(buffer, last=True):
                buffer = buffer[len(sentence) :]
                await self._speak(sentence, timeline, first=not spoke_anything)
                spoke_anything = True

            self.conversation_id = self._client.last_turn.conversation_id or self.conversation_id
            if not spoke_anything:
                await self._speak("我没听清，能再说一遍吗？", timeline, first=True)
            logger.info(f"[TURN {self.turns}] {timeline.report()}")
        except asyncio.CancelledError:
            logger.info("[BRAIN] answer cancelled")
            raise
        except BrainError as exc:
            # Never silent: the user is told, and the log keeps the cause.
            logger.error(f"[BRAIN] {type(exc).__name__}: {exc}")
            await self.push_frame(TTSSpeakFrame(exc.spoken, append_to_context=False))
        except Exception as exc:
            logger.exception(f"[BRAIN] unexpected failure: {type(exc).__name__}")
            await self.push_frame(
                TTSSpeakFrame("抱歉，出了点问题。", append_to_context=False)
            )
        finally:
            self._speaking = False
            if self._filler_task is not None:
                self._filler_task.cancel()
                self._filler_task = None

    async def _speak(self, sentence: str, timeline: TurnTimeline, *, first: bool) -> None:
        sentence = sentence.strip()
        if not sentence:
            return
        if first:
            timeline.mark("first_sentence")
        await self.push_frame(TTSSpeakFrame(sentence, append_to_context=False))
        if first:
            timeline.mark("tts_queued")
            logger.info(f"[BRAIN] first sentence handed to TTS: {sentence[:40]}")

    async def _speak_filler_later(self) -> None:
        try:
            await asyncio.sleep(self._filler.delay_secs)
            logger.info(f"[BRAIN] platform is slow -> filler: {self._filler.text}")
            await self.push_frame(
                TTSSpeakFrame(self._filler.text, append_to_context=False)
            )
        except asyncio.CancelledError:
            return

    async def _cancel_current(self) -> None:
        """Stop the in-flight answer, and tell the platform to stop generating too."""
        if self._filler_task is not None:
            self._filler_task.cancel()
            self._filler_task = None
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        task_id = self._client.last_turn.task_id
        if task_id:
            stopped = await self._client.stop(task_id)
            logger.info(f"[BRAIN] told the platform to stop (task={task_id[:8]}...): {stopped}")

    @staticmethod
    def _take_sentences(buffer: str, *, last: bool) -> list[str]:
        """Split off complete sentences; on the final flush, take whatever is left."""
        out: list[str] = []
        rest = buffer
        while True:
            match = _SENTENCE_END.search(rest)
            if match and match.end() > 0:
                out.append(rest[: match.end()])
                rest = rest[match.end() :]
                continue
            if len(rest) >= _MAX_BUFFER:
                out.append(rest[:_MAX_BUFFER])
                rest = rest[_MAX_BUFFER:]
                continue
            break
        if last and rest.strip():
            out.append(rest)
        return out
