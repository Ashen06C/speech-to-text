"""Progressive, non-overlapping CPU windows, using the existing ASRService."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from time import perf_counter
from typing import Literal
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from backend.asr_service import ASRService, InferenceError, ModelNotLoadedError
from backend.audio_buffer import AudioBuffer, AudioWindow, PCMError
from backend.config import Language, Settings

logger = logging.getLogger(__name__)


class StartConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["start"]
    language: Language
    sample_rate: StrictInt = Field(ge=16000, le=16000)
    channels: StrictInt = Field(ge=1, le=1)
    bit_depth: StrictInt = Field(ge=16, le=16)
    chunk_duration_ms: StrictInt = Field(ge=1, le=1000)


class StreamError(Exception):
    def __init__(self, code: str, message: str, close_code: int = 1008):
        super().__init__(message)
        self.code = code
        self.close_code = close_code


def parse_control(message: dict) -> dict:
    text = message.get("text")
    if text is None or len(text) > 4096:
        raise StreamError("invalid_message", "Expected a JSON control message (maximum 4096 characters).")
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise StreamError("invalid_message", "Control message is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise StreamError("invalid_message", "Control message must be a JSON object.")
    return data


@dataclass(frozen=True)
class PendingWindow:
    audio: AudioWindow
    enqueued_at: float


class StreamingSession:
    def __init__(self, websocket: WebSocket, service: ASRService, settings: Settings,
                 executor: ThreadPoolExecutor):
        self.websocket = websocket
        self.service = service
        self.settings = settings
        self.executor = executor
        self.stream_id = str(uuid4())
        self.status = "starting"
        self.connected = True
        self.config: StartConfig | None = None
        self.buffer: AudioBuffer | None = None
        self.started_at = 0.0
        self.first_transcript_at: float | None = None
        self.last_audio_at: float | None = None
        self.segment_index = 0
        self.segments: list[str] = []
        self.window_metrics: list[dict] = []
        # One reserved slot for the end sentinel; audio is limited to pending_windows.
        self.queue: asyncio.Queue[PendingWindow | None] = asyncio.Queue(settings.pending_windows + 1)
        self.tasks: list[asyncio.Task] = []
        self._send_lock = asyncio.Lock()

    async def send(self, event: dict) -> bool:
        async with self._send_lock:
            if not self.connected:
                raise WebSocketDisconnect()
            if self.status in {"cancelled", "failed"} and event["type"] not in {"error", "cancelled"}:
                return False
            try:
                await asyncio.wait_for(
                    self.websocket.send_json(event), self.settings.stream_send_timeout_seconds
                )
            except TimeoutError as exc:
                raise StreamError("slow_client", "Client is not receiving responses fast enough.", 1013) from exc
            except (WebSocketDisconnect, OSError):
                self.connected = False
                raise WebSocketDisconnect()
            return True

    async def receive(self, timeout: float | None = None) -> dict:
        try:
            seconds = self.settings.stream_idle_seconds if timeout is None else timeout
            message = await asyncio.wait_for(self.websocket.receive(), seconds or None)
        except TimeoutError as exc:
            raise StreamError("idle_timeout", "No message received before the session timeout.") from exc
        if message["type"] == "websocket.disconnect":
            self.connected = False
            raise WebSocketDisconnect(message.get("code", 1000))
        return message

    def enqueue(self, window: AudioWindow) -> None:
        if self.queue.qsize() >= self.settings.pending_windows:
            raise StreamError("overloaded", "Pending audio queue is full; stop and retry a new session.", 1013)
        self.queue.put_nowait(PendingWindow(window, perf_counter()))

    async def receive_audio(self) -> None:
        while True:
            message = await self.receive(timeout=0 if self.status == "draining" else None)
            if message.get("bytes") is not None:
                if self.status != "streaming":
                    raise StreamError("unexpected_message", "Audio is not accepted after end.")
                pcm = message["bytes"]
                if self.buffer.samples_received + len(pcm) // 2 > self.settings.max_stream_seconds * 16000:
                    raise StreamError("duration_limit", "Stream exceeds the configured audio duration limit.")
                try:
                    self.buffer.append(pcm)
                except PCMError as exc:
                    raise StreamError("invalid_chunk", str(exc)) from exc
                self.last_audio_at = perf_counter()
                while window := self.buffer.pop_window():
                    self.enqueue(window)
                continue

            control = parse_control(message)
            if control == {"type": "stop"}:
                self.status = "cancelled"
                await self.send({"type": "cancelled", "stream_id": self.stream_id,
                                 "message": "Session cancelled; pending audio discarded."})
                return
            if control == {"type": "end"} and self.status == "streaming":
                if self.buffer.samples_received == 0:
                    raise StreamError("empty_audio", "No audio samples were received.")
                if residual := self.buffer.pop_window(final=True):
                    self.enqueue(residual)
                self.status = "draining"
                self.queue.put_nowait(None)
                # Continue listening during drain so stop/disconnect is handled.
                continue
            raise StreamError("unexpected_message", "Expected audio, end, or stop in the current session state.")

    async def infer(self, window: AudioWindow):
        def call_model():
            dispatched_at = perf_counter()
            return self.service.transcribe(window.samples, self.config.language), dispatched_at

        # There is one streaming executor thread across all sessions, with at most
        # one submitted call per admitted session. REST uses the SAME ASR lock.
        concurrent_future = self.executor.submit(call_model)
        future = asyncio.wrap_future(concurrent_future)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Cancel an executor job that hasn't started. A running torch call
            # cannot be interrupted safely; retain ownership and wait for it.
            concurrent_future.cancel()
            try:
                await asyncio.shield(future)
            except (Exception, asyncio.CancelledError):
                pass
            raise

    async def transcribe_windows(self) -> None:
        while True:
            pending = await self.queue.get()
            if pending is None:
                break
            try:
                result, dispatched_at = await self.infer(pending.audio)
            except ModelNotLoadedError as exc:
                raise StreamError("model_unavailable", str(exc), 1011) from exc
            except InferenceError as exc:
                raise StreamError("inference_failed", str(exc), 1011) from exc
            started_at = result.inference_started_at
            queue_wait = max(0.0, (started_at if started_at is not None else dispatched_at) - pending.enqueued_at)
            text = result.text.strip()
            event = {
                "type": "segment_transcript", "stream_id": self.stream_id,
                "segment_index": self.segment_index, "language": self.config.language.value,
                "text": text,
                "audio_start_seconds": pending.audio.start_sample / 16000,
                "audio_end_seconds": pending.audio.end_sample / 16000,
                "inference_time_seconds": result.inference_time_seconds,
                "queue_wait_time_seconds": queue_wait,
            }
            if not await self.send(event):
                return
            if self.first_transcript_at is None:
                self.first_transcript_at = perf_counter()
            self.segments.append(text)
            self.window_metrics.append({
                "segment_index": self.segment_index,
                "inference_time_seconds": result.inference_time_seconds,
                "queue_wait_time_seconds": queue_wait,
            })
            self.segment_index += 1

        final_at = perf_counter()
        separator = "" if self.config.language == Language.CHINESE else " "
        if not await self.send({"type": "final_transcript", "stream_id": self.stream_id,
                         "language": self.config.language.value,
                         "text": separator.join(text for text in self.segments if text)}):
            return
        if not await self.send({
            "type": "metrics", "stream_id": self.stream_id,
            "time_to_first_transcript_seconds": self.first_transcript_at - self.started_at,
            "last_audio_to_final_seconds": final_at - self.last_audio_at,
            "stream_duration_seconds": final_at - self.started_at,
            "total_audio_duration_seconds": self.buffer.samples_received / 16000,
            "total_inference_time_seconds": sum(m["inference_time_seconds"] for m in self.window_metrics),
            "total_queue_wait_time_seconds": sum(m["queue_wait_time_seconds"] for m in self.window_metrics),
            "windows": self.window_metrics,
        }):
            return
        if not await self.send({"type": "completed", "stream_id": self.stream_id}):
            return
        self.status = "completed"

    async def cleanup(self) -> None:
        for task in self.tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        while not self.queue.empty():
            self.queue.get_nowait()
        if self.buffer is not None:
            self.buffer.clear()

    async def run(self) -> None:
        close_code = 1000
        try:
            start = parse_control(await self.receive(timeout=10))
            if start.get("type") != "start":
                raise StreamError("unexpected_message", "First message must be a start configuration.")
            try:
                self.config = StartConfig.model_validate(start)
            except ValidationError as exc:
                code = "unsupported_language" if any(e["loc"] == ("language",) for e in exc.errors()) else "invalid_config"
                raise StreamError(code, str(exc)) from exc
            if not self.service.model_loaded:
                raise StreamError("model_unavailable", "ASR model is not loaded. Check the server startup log.", 1011)
            self.buffer = AudioBuffer(16000, self.settings.window_ms, self.config.chunk_duration_ms)
            self.started_at = perf_counter()
            self.status = "streaming"
            await self.send({"type": "ready", "message": "Audio streaming session started",
                             "stream_id": self.stream_id})
            self.tasks = [asyncio.create_task(self.receive_audio(), name=f"receive-{self.stream_id}"),
                          asyncio.create_task(self.transcribe_windows(), name=f"asr-{self.stream_id}")]
            done, _ = await asyncio.wait(self.tasks, return_when=asyncio.FIRST_COMPLETED)
            if self.status == "cancelled":
                # Let the receiver finish the cancellation acknowledgement even
                # if the worker notices the terminal state first.
                await self.tasks[0]
            for task in done:
                task.result()
        except WebSocketDisconnect:
            if self.status not in {"completed", "cancelled"}:
                self.status = "disconnected"
        except Exception as exc:
            self.status = "failed"
            if not isinstance(exc, StreamError):
                logger.exception("Unexpected streaming failure: %s", self.stream_id)
                exc = StreamError("internal_error", "Streaming failed; check the server log.", 1011)
            close_code = exc.close_code
            if self.connected:
                try:
                    await self.send({"type": "error", "stream_id": self.stream_id,
                                     "code": exc.code, "message": str(exc)})
                except (StreamError, WebSocketDisconnect, RuntimeError):
                    self.connected = False
        finally:
            # Close promptly on stop/error, then wait safely for any running CPU
            # call before releasing this session and its admission slot.
            if self.connected:
                self.connected = False
                try:
                    await self.websocket.close(code=close_code)
                except (WebSocketDisconnect, RuntimeError, OSError):
                    pass
            await self.cleanup()


class StreamingService:
    def __init__(self, service: ASRService, settings: Settings):
        self.service = service
        self.settings = settings
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stream-asr")
        self.sessions: dict[str, StreamingSession] = {}

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
        if len(self.sessions) >= self.settings.max_streams:
            await websocket.send_json({"type": "error", "code": "overloaded",
                                       "message": "Maximum active streaming sessions reached; retry later."})
            await websocket.close(code=1013)
            return
        session = StreamingSession(websocket, self.service, self.settings, self.executor)
        self.sessions[session.stream_id] = session
        try:
            await session.run()
        finally:
            self.sessions.pop(session.stream_id, None)

    async def shutdown(self) -> None:
        # Lifespan shutdown occurs after Uvicorn closes active connections.
        for session in list(self.sessions.values()):
            await session.cleanup()
        await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=True)
