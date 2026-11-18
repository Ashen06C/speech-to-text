"""Functional streaming tests with mock inference; no throughput benchmarks."""

import asyncio
import threading
import unittest
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.asr_service import ASRService, InferenceError, Transcription
from backend.audio_buffer import AudioBuffer, PCMError
from backend.config import Language, Settings
from backend.main import create_app
from backend.tests.test_api import make_wav

CHUNK = bytes(16000)
START = {"type": "start", "language": "English", "sample_rate": 16000,
         "channels": 1, "bit_depth": 16, "chunk_duration_ms": 500}


class FakeASR:
    def __init__(self):
        self.model_loaded = False
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.fail = False

    def load_model(self):
        self.model_loaded = True

    def unload_model(self):
        self.model_loaded = False

    def transcribe(self, samples, language):
        started = perf_counter()
        self.entered.set()
        if not self.release.wait(5):
            raise InferenceError("Mock inference was not released.")
        if self.fail:
            raise InferenceError("Simulated inference failure.")
        self.calls.append((samples.copy(), language))
        text = "你好" if language == Language.CHINESE else f"segment{len(self.calls) - 1}"
        return Transcription(text, perf_counter() - started, started)


class AudioBufferTests(unittest.TestCase):
    def test_window_and_residual_offsets(self):
        buffer = AudioBuffer(16000, 3000, 500)
        for _ in range(5):
            buffer.append(CHUNK)
        self.assertIsNone(buffer.pop_window())
        buffer.append(CHUNK)
        first = buffer.pop_window()
        self.assertEqual((first.start_sample, first.end_sample), (0, 48000))
        self.assertEqual(first.samples.dtype, np.float32)
        buffer.append(bytes(2000))
        self.assertIsNone(buffer.pop_window())
        final = buffer.pop_window(final=True)
        self.assertEqual((final.start_sample, final.end_sample), (48000, 49000))
        self.assertIsNone(buffer.pop_window(final=True))

    def test_little_endian_float32_conversion(self):
        buffer = AudioBuffer(16000, 3000, 500)
        buffer.append(np.array([-32768, -1, 0, 32767], dtype="<i2").tobytes())
        audio = buffer.pop_window(final=True).samples
        np.testing.assert_array_equal(audio, np.array([-1, -1/32768, 0, 32767/32768], dtype=np.float32))

    def test_bad_chunks(self):
        for pcm in (b"", b"a", bytes(16002), b"RIFF1234WAVE" + bytes(100)):
            with self.subTest(size=len(pcm)), self.assertRaises(PCMError):
                AudioBuffer(16000, 3000, 500).append(pcm)

    def test_short_chunk_must_be_last(self):
        buffer = AudioBuffer(16000, 3000, 500)
        buffer.append(bytes(100))
        with self.assertRaises(PCMError):
            buffer.append(CHUNK)


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeASR()
        self.app = create_app(Settings(), self.service)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.addCleanup(self.service.release.set)

    def start(self, ws, **changes):
        ws.send_json(START | changes)
        ready = ws.receive_json()
        self.assertEqual(ready["type"], "ready", ready)
        self.assertTrue(ready["stream_id"])
        return ready

    def collect(self, ws):
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] in {"completed", "error", "cancelled"}:
                return events

    def wait_until(self, predicate):
        async def check():
            async with asyncio.timeout(3):
                while not predicate():
                    await asyncio.sleep(0.005)
        self.client.portal.call(check)

    def test_connection_and_stop(self):
        with self.client.websocket_connect("/ws/transcribe") as ws:
            self.start(ws)
            ws.send_json({"type": "stop"})
            self.assertEqual(ws.receive_json()["type"], "cancelled")
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()
        self.assertEqual(self.service.calls, [])

    def test_invalid_configuration(self):
        for change in ({"sample_rate": 44100}, {"channels": 2}, {"bit_depth": 24},
                       {"chunk_duration_ms": 0}, {"chunk_duration_ms": 1001},
                       {"sample_rate": "16000"}, {"extra": True}):
            with self.subTest(change=change), self.client.websocket_connect("/ws/transcribe") as ws:
                ws.send_json(START | change)
                self.assertEqual(ws.receive_json()["code"], "invalid_config")

    def test_unsupported_language(self):
        with self.client.websocket_connect("/ws/transcribe") as ws:
            ws.send_json(START | {"language": "French"})
            self.assertEqual(ws.receive_json()["code"], "unsupported_language")

    def test_first_message_must_be_start(self):
        with self.client.websocket_connect("/ws/transcribe") as ws:
            ws.send_json({"type": "end"})
            self.assertEqual(ws.receive_json()["code"], "unexpected_message")

    def test_binary_before_start(self):
        with self.client.websocket_connect("/ws/transcribe") as ws:
            ws.send_bytes(CHUNK)
            self.assertEqual(ws.receive_json()["code"], "invalid_message")

    def test_invalid_json(self):
        for text in ("{", "[]", "x" * 4097):
            with self.subTest(text=text[:10]), self.client.websocket_connect("/ws/transcribe") as ws:
                ws.send_text(text)
                self.assertEqual(ws.receive_json()["code"], "invalid_message")

    def test_invalid_pcm_chunks(self):
        for pcm in (b"", b"1", bytes(16002), b"RIFF1234WAVE" + bytes(100)):
            with self.subTest(size=len(pcm)), self.client.websocket_connect("/ws/transcribe") as ws:
                self.start(ws)
                ws.send_bytes(pcm)
                self.assertEqual(ws.receive_json()["code"], "invalid_chunk")

    def test_empty_stream(self):
        with self.client.websocket_connect("/ws/transcribe") as ws:
            self.start(ws)
            ws.send_json({"type": "end"})
            self.assertEqual(ws.receive_json()["code"], "empty_audio")

    def test_order_residual_metrics_and_no_duplicates(self):
        with self.client.websocket_connect("/ws/transcribe") as ws:
            ready = self.start(ws)
            for _ in range(12):
                ws.send_bytes(CHUNK)
            ws.send_bytes(bytes(4000))
            ws.send_json({"type": "end"})
            events = self.collect(ws)
        segments = [event for event in events if event["type"] == "segment_transcript"]
        self.assertEqual([event["segment_index"] for event in segments], [0, 1, 2])
        self.assertEqual([event["audio_start_seconds"] for event in segments], [0, 3, 6])
        self.assertEqual([event["audio_end_seconds"] for event in segments], [3, 6, 6.125])
        self.assertTrue(all(event["stream_id"] == ready["stream_id"] for event in events))
        self.assertEqual([len(call[0]) for call in self.service.calls], [48000, 48000, 2000])
        self.assertEqual(events[-3]["text"], "segment0 segment1 segment2")
        self.assertEqual([event["type"] for event in events[-3:]], ["final_transcript", "metrics", "completed"])
        metrics = events[-2]
        self.assertEqual(metrics["total_audio_duration_seconds"], 6.125)
        self.assertGreaterEqual(metrics["time_to_first_transcript_seconds"], 0)
        self.assertGreaterEqual(metrics["last_audio_to_final_seconds"], 0)
        self.assertEqual(len(metrics["windows"]), 3)

    def test_final_small_window(self):
        with self.client.websocket_connect("/ws/transcribe") as ws:
            self.start(ws)
            ws.send_bytes(bytes(1600))
            ws.send_json({"type": "end"})
            events = self.collect(ws)
        self.assertEqual(events[0]["audio_end_seconds"], 0.05)
        self.assertEqual(len(self.service.calls), 1)

    def test_configurable_chunk_and_window(self):
        app = create_app(Settings(window_ms=1000), self.service)
        with TestClient(app) as client, client.websocket_connect("/ws/transcribe") as ws:
            self.start(ws, chunk_duration_ms=250)
            for _ in range(4):
                ws.send_bytes(bytes(8000))
            ws.send_json({"type": "end"})
            events = self.collect(ws)
        self.assertEqual(events[0]["audio_end_seconds"], 1)

    def test_chinese_concatenation(self):
        with self.client.websocket_connect("/ws/transcribe") as ws:
            self.start(ws, language="Chinese")
            for _ in range(7):
                ws.send_bytes(CHUNK)
            ws.send_json({"type": "end"})
            events = self.collect(ws)
        self.assertEqual(events[-3]["text"], "你好你好")

    def test_receive_while_inference_runs(self):
        self.service.release.clear()
        with self.client.websocket_connect("/ws/transcribe") as ws:
            self.start(ws)
            for _ in range(6):
                ws.send_bytes(CHUNK)
            self.assertTrue(self.service.entered.wait(2))
            ws.send_bytes(CHUNK)
            self.wait_until(lambda: next(iter(self.app.state.streaming_service.sessions.values())).buffer.samples_received == 56000)
            self.service.release.set()
            ws.send_json({"type": "end"})
            self.assertEqual(self.collect(ws)[-1]["type"], "completed")

    def test_stop_during_inference_discards_pending(self):
        self.service.release.clear()
        with self.client.websocket_connect("/ws/transcribe") as ws:
            self.start(ws)
            for _ in range(6):
                ws.send_bytes(CHUNK)
            self.assertTrue(self.service.entered.wait(2))
            for _ in range(6):
                ws.send_bytes(CHUNK)
            ws.send_json({"type": "stop"})
            self.assertEqual(ws.receive_json()["type"], "cancelled")
            self.service.release.set()
            self.wait_until(lambda: not self.app.state.streaming_service.sessions)
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()
        self.assertEqual(len(self.service.calls), 1)

    def test_disconnect_cleans_inflight_and_pending_work(self):
        self.service.release.clear()
        with self.client.websocket_connect("/ws/transcribe") as ws:
            self.start(ws)
            for _ in range(6):
                ws.send_bytes(CHUNK)
            self.assertTrue(self.service.entered.wait(2))
            session = next(iter(self.app.state.streaming_service.sessions.values()))
            ws.close()
            self.service.release.set()
            self.wait_until(lambda: not self.app.state.streaming_service.sessions)
            self.assertTrue(all(task.done() for task in session.tasks))
            self.assertTrue(session.queue.empty())
            self.assertEqual(session.status, "disconnected")

    def test_stop_while_draining(self):
        self.service.release.clear()
        with self.client.websocket_connect("/ws/transcribe") as ws:
            self.start(ws)
            ws.send_bytes(CHUNK)
            ws.send_json({"type": "end"})
            self.assertTrue(self.service.entered.wait(2))
            ws.send_json({"type": "stop"})
            self.assertEqual(ws.receive_json()["type"], "cancelled")
            self.service.release.set()
            self.wait_until(lambda: not self.app.state.streaming_service.sessions)

    def test_queue_overload(self):
        self.service.release.clear()
        with self.client.websocket_connect("/ws/transcribe") as ws:
            self.start(ws)
            for _ in range(6):
                ws.send_bytes(CHUNK)
            self.assertTrue(self.service.entered.wait(2))
            for _ in range(30):
                ws.send_bytes(CHUNK)
            self.assertEqual(ws.receive_json()["code"], "overloaded")
            self.service.release.set()
            self.wait_until(lambda: not self.app.state.streaming_service.sessions)

    def test_session_admission_limit_and_isolation(self):
        with self.client.websocket_connect("/ws/transcribe") as first, self.client.websocket_connect("/ws/transcribe") as second:
            first_id = self.start(first)["stream_id"]
            second_id = self.start(second, language="Indonesian")["stream_id"]
            self.assertNotEqual(first_id, second_id)
            with self.client.websocket_connect("/ws/transcribe") as third:
                self.assertEqual(third.receive_json()["code"], "overloaded")
            first.send_json({"type": "stop"})
            second.send_json({"type": "stop"})
            self.assertEqual(first.receive_json()["stream_id"], first_id)
            self.assertEqual(second.receive_json()["stream_id"], second_id)

    def test_inference_failure(self):
        self.service.fail = True
        with self.client.websocket_connect("/ws/transcribe") as ws:
            self.start(ws)
            ws.send_bytes(CHUNK)
            ws.send_json({"type": "end"})
            self.assertEqual(ws.receive_json()["code"], "inference_failed")

    def test_model_unavailable(self):
        self.service.model_loaded = False
        with self.client.websocket_connect("/ws/transcribe") as ws:
            ws.send_json(START)
            self.assertEqual(ws.receive_json()["code"], "model_unavailable")

    def test_unexpected_message_after_start(self):
        with self.client.websocket_connect("/ws/transcribe") as ws:
            self.start(ws)
            ws.send_json(START)
            self.assertEqual(ws.receive_json()["code"], "unexpected_message")

    def test_audio_after_end(self):
        self.service.release.clear()
        with self.client.websocket_connect("/ws/transcribe") as ws:
            self.start(ws)
            ws.send_bytes(CHUNK)
            ws.send_json({"type": "end"})
            self.assertTrue(self.service.entered.wait(2))
            ws.send_bytes(CHUNK)
            self.assertEqual(ws.receive_json()["code"], "unexpected_message")
            self.service.release.set()
            self.wait_until(lambda: not self.app.state.streaming_service.sessions)

    def test_duration_limit(self):
        with TestClient(create_app(Settings(max_stream_seconds=1), self.service)) as client:
            with client.websocket_connect("/ws/transcribe") as ws:
                self.start(ws)
                for _ in range(3):
                    ws.send_bytes(CHUNK)
                self.assertEqual(ws.receive_json()["code"], "duration_limit")


class SharedModelTests(unittest.TestCase):
    def test_rest_and_websocket_share_one_model_and_lock(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def model_transcribe(**kwargs):
            calls.append(kwargs)
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release inference")
            return [SimpleNamespace(text="Shared model")]

        model = SimpleNamespace(transcribe=model_transcribe)
        service = ASRService(Settings())
        with patch.object(service, "_create_model", return_value=model) as loader:
            with TestClient(create_app(service=service)) as client:
                with client.websocket_connect("/ws/transcribe") as ws:
                    ws.send_json(START)
                    self.assertEqual(ws.receive_json()["type"], "ready")
                    for _ in range(6):
                        ws.send_bytes(CHUNK)
                    self.assertTrue(entered.wait(3))
                    # A second call cannot acquire the same lock while WS owns it.
                    self.assertFalse(service._lock.acquire(blocking=False))
                    release.set()
                    ws.send_json({"type": "end"})
                    while ws.receive_json()["type"] != "completed":
                        pass
                response = client.post("/api/transcribe", data={"language": "English"},
                                       files={"file": ("test.wav", make_wav(), "audio/wav")})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["transcript"], "Shared model")
                loader.assert_called_once()
                self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
