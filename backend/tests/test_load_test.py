"""Concurrency tool tests using mocked clients/resources, never the Qwen model."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import soundfile as sf
from websockets.asyncio.server import serve

from backend.benchmarks.benchmark_metrics import aggregate_resources
from backend.benchmarks.load_test import run_round
from backend.benchmarks.load_test_metrics import Limits, aggregate_level, assess_round, percentile, select_capacity
from backend.benchmarks.load_test_report import save_load_results


class FakeMonitor:
    instances = []

    def __init__(self, *args):
        self.errors = []
        self.stopped = False
        self.samples = [{"stage": stage, "elapsed_seconds": i * .5, "interval_seconds": .5 if i else 0,
                         "total_ram_bytes": 16*1024**3, "available_ram_bytes": 8*1024**3,
                         "process_rss_bytes": 3*1024**3, "process_cpu_percent": 300 if i else None,
                         "system_cpu_percent": 30 if i else None} for i, stage in enumerate(("before", "during", "after"))]
        self.instances.append(self)

    def start(self): pass

    def stop(self):
        self.stopped = True
        return {"samples": self.samples, "summary": aggregate_resources(self.samples), "errors": self.errors}


SAMPLE = {"language": "English", "audio": "mock.wav", "duration_seconds": 3.5}


def successful_stream():
    state = {"count": 0, "active": 0, "peak": 0}

    async def stream(*args, observation, start_event, **kwargs):
        state["count"] += 1
        index = state["count"]
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        try:
            await start_event.wait()
            await asyncio.sleep(.02)
            stream_id = f"stream-{index}"
            events = [{"type": "segment_transcript", "stream_id": stream_id, "language": "English", "segment_index": i,
                       "text": text, "audio_start_seconds": start, "audio_end_seconds": end,
                       "inference_time_seconds": .3, "queue_wait_time_seconds": .1}
                      for i, (text, start, end) in enumerate([(f"call{index}", 0, 3), (f"end{index}", 3, 3.5)])]
            events += [{"type": "final_transcript", "stream_id": stream_id, "language": "English", "text": f"call{index} end{index}"},
                       {"type": "metrics", "stream_id": stream_id, "total_audio_duration_seconds": 3.5},
                       {"type": "completed", "stream_id": stream_id}]
            observation.update(events=events, ready={"stream_id": stream_id}, connection_success=True,
                               bytes_sent=112000, chunks_sent=7, timing={"connection_attempt_at": 10+index*.001,
                                   "stream_started_at": 10, "last_audio_sent_at": 13.5, "end_sent_at": 13.5,
                                   "first_transcript_at": 13.2, "final_transcript_at": 14, "completed_at": 14.1})
            return observation
        finally:
            state["active"] -= 1
    return stream, state


class MetricTests(unittest.TestCase):
    def test_percentiles(self):
        self.assertEqual(percentile([1, 2, 3, 4], 50), 2.5)
        self.assertAlmostEqual(percentile([1, 2, 3, 4], 95), 3.85)
        self.assertEqual(percentile([7], 95), 7)
        self.assertIsNone(percentile([], 50))

    def test_capacity_selection_does_not_claim_maximum(self):
        result = select_capacity([{"concurrency": 1, "meets_criteria": True, "all_runs_successful": True},
                                  {"concurrency": 2, "meets_criteria": False, "all_runs_successful": True}])
        self.assertEqual(result["highest_attempted"], 2)
        self.assertEqual(result["highest_successful"], 2)
        self.assertEqual(result["highest_meeting_criteria"], 1)
        self.assertTrue(result["next_higher_tested"])
        self.assertFalse(result["maximum_established"])

    def test_missing_capacity_not_reported_as_zero_hardware_capacity(self):
        self.assertIsNone(select_capacity([])["highest_meeting_criteria"])

    def test_invalid_guardrails(self):
        for kwargs in ({"cpu_percent": 101}, {"ram_percent": 0}, {"finalization_seconds": float("nan")}):
            with self.assertRaises(ValueError):
                Limits(**kwargs)


class RoundTests(unittest.IsolatedAsyncioTestCase):
    async def run_fake(self, n=2, stream=None, **kwargs):
        stream = stream or successful_stream()[0]
        return await run_round(SAMPLE, n, 1, "ws://mock", 1, Limits(), .1, kwargs.pop("timeout", 1), 3000,
                               monitor_factory=kwargs.pop("monitor_factory", FakeMonitor), stream=stream, cleanup_grace=0)

    async def test_clients_are_simultaneous_and_transcripts_independent(self):
        stream, state = successful_stream()
        result = await self.run_fake(n=4, stream=stream)
        self.assertEqual(state["count"], 4)
        self.assertEqual(state["peak"], 4)
        self.assertEqual(state["active"], 0)
        self.assertEqual(len({c["stream_id"] for c in result["calls"]}), 4)
        self.assertEqual(len({c["transcript"] for c in result["calls"]}), 4)
        self.assertTrue(result["acceptance"]["meets_criteria"])
        self.assertTrue(FakeMonitor.instances[-1].stopped)

    async def test_connection_failure_counting(self):
        async def disconnected(*args, start_event, **kwargs):
            await start_event.wait()
            raise ConnectionRefusedError("backend unavailable")
        result = await self.run_fake(stream=disconnected)
        summary = aggregate_level([result], 1)
        self.assertEqual(summary["completed_streams"], 0)
        self.assertEqual(summary["failed_streams"], 2)
        self.assertEqual(summary["success_rate"], 0)
        self.assertIsNone(summary["finalization_from_end_seconds"]["mean"])

    async def test_timeout_and_cleanup(self):
        cleaned = []
        async def stalled(*args, start_event, **kwargs):
            await start_event.wait()
            try:
                await asyncio.sleep(10)
            finally:
                cleaned.append(True)
        result = await self.run_fake(stream=stalled, timeout=.01)
        self.assertEqual(len(cleaned), 2)
        self.assertTrue(all("TimeoutError" in c["errors"][0] for c in result["calls"]))

    async def test_operator_cancellation_stops_clients_and_monitor(self):
        cleaned = []
        async def stalled(*args, start_event, **kwargs):
            await start_event.wait()
            try:
                await asyncio.sleep(10)
            finally:
                cleaned.append(True)
        task = asyncio.create_task(self.run_fake(stream=stalled))
        await asyncio.sleep(.03)
        task.cancel()
        result = await task
        self.assertEqual(len(cleaned), 2)
        self.assertTrue(result["operator_cancelled"])
        self.assertTrue(all(c["status"] == "CANCELLED" for c in result["calls"]))
        self.assertTrue(FakeMonitor.instances[-1].stopped)

    async def test_memory_preflight_never_connects_or_writes_results(self):
        class Unsafe(FakeMonitor):
            def __init__(self, *args):
                super().__init__(*args)
                for sample in self.samples:
                    sample["available_ram_bytes"] = 100*1024**2
        async def unexpected(*args, **kwargs):
            self.fail("Unsafe preflight must not open a connection")
        result = await self.run_fake(stream=unexpected, monitor_factory=Unsafe)
        self.assertIn("preflight_refusal", result)
        self.assertEqual(result["calls"], [])
        with tempfile.TemporaryDirectory() as directory:
            save_load_results({"rounds": []}, Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    async def test_sustained_cpu_guard_cancels_clients(self):
        class Hot(FakeMonitor):
            def __init__(self, *args):
                super().__init__(*args)
                for sample in self.samples[1:]:
                    sample.update(system_cpu_percent=95, interval_seconds=2)
        async def stalled(*args, start_event, **kwargs):
            await start_event.wait()
            await asyncio.sleep(10)
        result = await self.run_fake(stream=stalled, monitor_factory=Hot)
        self.assertTrue(result["guardrail_reasons"])
        self.assertTrue(all(c["status"] == "CANCELLED" for c in result["calls"]))

    async def test_repetitions_required_for_capacity(self):
        result = await self.run_fake()
        incomplete = aggregate_level([result], 3)
        complete = aggregate_level([result, result, result], 3)
        self.assertFalse(incomplete["meets_criteria"])
        self.assertTrue(complete["meets_criteria"])
        self.assertEqual(complete["resources"]["process_cpu_percent"]["mean"], 300)

    async def test_acceptance_rejects_latency_queue_and_demand(self):
        result = await self.run_fake()
        result["calls"][0]["client_latency"]["finalization_from_end_seconds"] = 6
        result["calls"][0]["windows"][0]["queue_wait_time_seconds"] = 7
        result["inference_demand_ratio"] = 2
        verdict = assess_round(result, Limits())
        self.assertFalse(verdict["meets_criteria"])
        self.assertTrue(any("Finalization" in r for r in verdict["reasons"]))
        self.assertTrue(any("queue" in r for r in verdict["reasons"]))
        self.assertTrue(any("inference" in r for r in verdict["reasons"]))

    async def test_duplicate_ids_are_rejected(self):
        stream, _ = successful_stream()
        async def duplicate(*args, **kwargs):
            result = await stream(*args, **kwargs)
            for event in result["events"]:
                event["stream_id"] = "same-id"
            return result
        result = await self.run_fake(stream=duplicate)
        self.assertTrue(all(c["status"] == "FAILED" for c in result["calls"]))

    async def test_final_transcript_must_match_connection_ready_id(self):
        stream, _ = successful_stream()
        async def wrong_session(*args, **kwargs):
            output = await stream(*args, **kwargs)
            output["ready"] = {"stream_id": "different-session"}
            return output
        result = await self.run_fake(stream=wrong_session)
        self.assertTrue(all("ready stream ID" in c["errors"][0] for c in result["calls"]))

    async def test_partial_first_text_survives_later_failure(self):
        async def partial(*args, observation, start_event, **kwargs):
            await start_event.wait()
            observation.update(connection_success=True, ready={"stream_id": "partial"}, bytes_sent=16000, chunks_sent=1,
                               timing={"stream_started_at": 10, "first_transcript_at": 12, "last_audio_sent_at": 13},
                               events=[{"type": "segment_transcript", "text": "hello"}])
            raise RuntimeError("later disconnect")
        result = await self.run_fake(n=1, stream=partial)
        self.assertEqual(result["calls"][0]["client_latency"]["time_to_first_transcript_seconds"], 2)
        self.assertFalse(result["calls"][0]["final_transcript_received"])
        self.assertEqual(result["calls"][0]["bytes_sent"], 16000)

    async def test_four_real_websockets_with_mock_server(self):
        # Exercises actual client sockets and PCM pacing, without any AI model.
        connections, payloads = [], []
        async def server(ws):
            start = json.loads(await ws.recv())
            self.assertEqual(start["type"], "start")
            stream_id = f"socket-{len(connections)}"
            connections.append(stream_id)
            await ws.send(json.dumps({"type": "ready", "stream_id": stream_id}))
            pcm = await ws.recv()
            payloads.append(pcm)
            self.assertEqual(json.loads(await ws.recv())["type"], "end")
            events = [
                {"type": "segment_transcript", "stream_id": stream_id, "segment_index": 0, "language": "English",
                 "text": stream_id, "audio_start_seconds": 0, "audio_end_seconds": .1,
                 "inference_time_seconds": .005, "queue_wait_time_seconds": 0},
                {"type": "final_transcript", "stream_id": stream_id, "language": "English", "text": stream_id},
                {"type": "metrics", "stream_id": stream_id, "total_audio_duration_seconds": .1},
                {"type": "completed", "stream_id": stream_id},
            ]
            for event in events:
                await ws.send(json.dumps(event))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.wav"
            sf.write(path, np.zeros(1600), 16000, subtype="PCM_16")
            async with serve(server, "127.0.0.1", 0) as sockets:
                port = sockets.sockets[0].getsockname()[1]
                result = await run_round({"language": "English", "audio": str(path), "duration_seconds": .1},
                    4, 1, f"ws://127.0.0.1:{port}", 1, Limits(), .1, 5, 3000, monitor_factory=FakeMonitor, cleanup_grace=0)
        self.assertEqual(len(connections), 4)
        self.assertEqual([len(p) for p in payloads], [3200]*4)
        self.assertTrue(all(c["status"] == "COMPLETED" for c in result["calls"]))
        self.assertEqual(len({c["transcript"] for c in result["calls"]}), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
