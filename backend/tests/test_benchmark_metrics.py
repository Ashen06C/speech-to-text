"""Metric, export, monitoring, and client tests; never load an ASR model."""

import asyncio
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np
import soundfile as sf
from websockets.asyncio.server import serve

from backend.benchmarks.accuracy_metrics import normalize, score_accuracy
from backend.benchmarks.benchmark import benchmark_run
from backend.benchmarks.benchmark_metrics import ResourceMonitor, aggregate_resources, client_latencies, inference_rtf
from backend.benchmarks.benchmark_report import save_results, summarize
from backend.test_streaming_client import stream_file


class AccuracyTests(unittest.TestCase):
    def test_english_wer_and_counts(self):
        result = score_accuracy("English", "one two three", "one four three five", verified=True)
        self.assertAlmostEqual(result["error_rate"], 2/3)
        self.assertEqual((result["substitutions"], result["deletions"], result["insertions"]), (1, 0, 1))

    def test_indonesian_wer(self):
        result = score_accuracy("Indonesian", "Saya makan nasi", "saya nasi", verified=True)
        self.assertAlmostEqual(result["error_rate"], 1/3)
        self.assertEqual(result["deletions"], 1)

    def test_chinese_cer_and_unicode(self):
        result = score_accuracy("Chinese", "你好世界。", "你好世人！", verified=True)
        self.assertEqual(result["error_rate"], 0.25)
        self.assertEqual(result["substitutions"], 1)
        self.assertEqual(result["reference_units"], 4)

    def test_empty_hypothesis_is_all_deletions(self):
        for language, reference, count in [("English", "hello world", 2), ("Chinese", "你好", 2)]:
            result = score_accuracy(language, reference, "", verified=True)
            self.assertEqual(result["error_rate"], 1)
            self.assertEqual(result["deletions"], count)

    def test_missing_unverified_and_normalized_empty_reference(self):
        for reference, verified in [(None, True), ("", True), ("words", False), ("。！", True)]:
            result = score_accuracy("Chinese", reference, "你好", verified=verified)
            self.assertEqual(result["status"], "NOT MEASURED")
            self.assertIsNone(result["error_rate"])

    def test_normalization_preserves_word_boundaries_and_contractions(self):
        self.assertEqual(normalize("  HELLO,world! Don't  stop. ", "English"), "hello world dont stop")
        self.assertEqual(normalize("你 好，世界！Ａ", "Chinese"), "你好世界a")

    def test_error_rate_can_exceed_one(self):
        result = score_accuracy("English", "one", "one two three", verified=True)
        self.assertEqual(result["error_rate"], 2)


class CalculationTests(unittest.TestCase):
    def test_rtf(self):
        self.assertEqual(inference_rtf(6, 10), .6)
        self.assertEqual(inference_rtf(0, 10), 0)
        for inference, duration in [(-1, 10), (1, 0), (float("nan"), 10), (1, float("inf"))]:
            with self.assertRaises(ValueError):
                inference_rtf(inference, duration)

    def test_client_latency_definitions(self):
        result = client_latencies({"stream_started_at": 100, "last_audio_sent_at": 110, "end_sent_at": 111,
                                   "first_transcript_at": 104, "final_transcript_at": 114, "completed_at": 115})
        self.assertEqual(result, {"time_to_first_transcript_seconds": 4, "first_transcript_before_last_audio": True,
                                 "finalization_from_end_seconds": 3, "finalization_from_last_audio_seconds": 4,
                                 "total_end_to_end_seconds": 15, "audio_transmission_seconds": 10})

    def test_no_nonempty_transcript_has_no_first_latency(self):
        result = client_latencies({"stream_started_at": 0, "last_audio_sent_at": 1, "end_sent_at": 1,
                                   "final_transcript_at": 2, "completed_at": 2})
        self.assertIsNone(result["time_to_first_transcript_seconds"])
        self.assertIsNone(result["first_transcript_before_last_audio"])

    def test_bad_timestamp_order_is_rejected(self):
        with self.assertRaises(ValueError):
            client_latencies({"stream_started_at": 3, "last_audio_sent_at": 2, "end_sent_at": 2,
                              "final_transcript_at": 4, "completed_at": 5})

    def test_cpu_weighted_mean_and_memory_aggregation(self):
        samples = [
            {"interval_seconds": 0, "process_cpu_percent": None, "system_cpu_percent": None, "process_rss_bytes": 10*1024**2, "available_ram_bytes": 100*1024**2},
            {"interval_seconds": .5, "process_cpu_percent": 200, "system_cpu_percent": 20, "process_rss_bytes": 20*1024**2, "available_ram_bytes": 90*1024**2},
            {"interval_seconds": 1.5, "process_cpu_percent": 400, "system_cpu_percent": 40, "process_rss_bytes": 30*1024**2, "available_ram_bytes": 80*1024**2},
        ]
        result = aggregate_resources(samples)
        self.assertEqual(result["process_cpu_percent"], {"mean": 350, "peak": 400})
        self.assertEqual(result["system_cpu_percent"], {"mean": 35, "peak": 40})
        self.assertEqual(result["process_rss_mib"], {"count": 3, "mean": 20, "min": 10, "max": 30})
        self.assertEqual(result["available_ram_mib"]["min"], 80)

    def test_no_cpu_samples_not_reported_as_zero(self):
        self.assertIsNone(aggregate_resources([])["process_cpu_percent"]["mean"])

    def test_summary_excludes_failed_runs(self):
        failed = {"language": "English", "status": "FAILED"}
        result = summarize([failed])[0]
        self.assertEqual(result["failed_runs"], 1)
        self.assertEqual(result["inference_rtf"]["count"], 0)
        self.assertIsNone(result["mean_error_rate"]["mean"])

    def test_strict_json_and_unicode_csv_serialization(self):
        result = {"created_at": "test", "system": {}, "runs": [{"language": "Chinese", "audio": "test.wav", "run": 1,
                  "status": "FAILED", "errors": ["模拟错误"], "client_observation": {"events": []}}],
                  "warmups": [], "setup_errors": [], "configuration": {}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            save_results(result, output)
            self.assertEqual(json.loads((output / "benchmark_results.json").read_text(encoding="utf-8")), result)
            rows = list(csv.DictReader(io.StringIO((output / "benchmark_results.csv").read_text(encoding="utf-8-sig"))))
            self.assertEqual(rows[0]["errors"], "模拟错误")
            self.assertEqual(rows[0]["accuracy_status"], "NOT MEASURED")
            self.assertIn("FAILED", (output / "benchmark_summary.md").read_text(encoding="utf-8"))
            result["bad"] = float("nan")
            with self.assertRaises(ValueError):
                save_results(result, output)


class AsyncBenchmarkTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_session_records_error_partial_events_and_stops_monitor(self):
        class Monitor:
            stopped = False
            def __init__(self, *args): pass
            def start(self): pass
            def stop(self):
                Monitor.stopped = True
                return {"errors": [], "samples": [], "summary": aggregate_resources([])}

        async def broken_stream(*args, observation, **kwargs):
            observation["events"] = [{"type": "error", "code": "inference_failed"}]
            raise RuntimeError("Model inference failed")

        sample = {"language": "English", "audio": "synthetic.wav", "format_status": "PASS", "duration_seconds": 1}
        result = await benchmark_run(sample, 1, "ws://test", 1, .5, 10, monitor_factory=Monitor, stream=broken_stream)
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("Model inference failed", result["errors"][0])
        self.assertTrue(Monitor.stopped)
        self.assertEqual(result["client_observation"]["events"][0]["code"], "inference_failed")
        self.assertNotIn("inference_rtf", result)

    async def test_invalid_audio_never_connects(self):
        async def unexpected(*args, **kwargs):
            self.fail("Invalid audio must not open a WebSocket")
        result = await benchmark_run({"language": "Chinese", "audio": "missing.wav", "format_status": "NOT TESTED",
                                      "errors": ["missing"]}, 1, "ws://test", 1, .5, 10, stream=unexpected)
        self.assertEqual(result["status"], "FAILED")

    async def test_monitor_primes_counters_collects_samples_and_stops(self):
        monitor = ResourceMonitor(os.getpid(), .1)
        monitor.start()
        await asyncio.sleep(.25)
        result = monitor.stop()
        self.assertEqual(result["errors"], [])
        self.assertIsNone(result["samples"][0]["process_cpu_percent"])
        self.assertEqual(result["samples"][0]["stage"], "before")
        self.assertEqual(result["samples"][-1]["stage"], "after")
        self.assertGreaterEqual(result["summary"]["cpu_sample_count"], 2)
        self.assertFalse(result["children_included"])
        self.assertFalse(monitor._thread.is_alive())

    async def test_real_client_pacing_and_first_nonempty_timing_with_fake_server(self):
        received = []
        async def server(ws):
            start = json.loads(await ws.recv())
            self.assertEqual(start["language"], "Chinese")
            await ws.send(json.dumps({"type": "ready", "stream_id": "mock"}))
            while True:
                message = await ws.recv()
                if isinstance(message, bytes):
                    received.append(message)
                    await ws.send(json.dumps({"type": "segment_transcript", "segment_index": len(received)-1,
                        "text": "" if len(received) == 1 else "你好", "inference_time_seconds": .01, "queue_wait_time_seconds": 0}))
                else:
                    self.assertEqual(json.loads(message)["type"], "end")
                    for event in ({"type": "final_transcript", "text": "你好"}, {"type": "metrics"}, {"type": "completed"}):
                        await ws.send(json.dumps(event))
                    return
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.wav"
            sf.write(path, np.zeros(17600), 16000, subtype="PCM_16")
            async with serve(server, "127.0.0.1", 0) as websocket_server:
                port = websocket_server.sockets[0].getsockname()[1]
                output = await stream_file(path, "Chinese", 500, f"ws://127.0.0.1:{port}", verbose=False)
        self.assertEqual([len(chunk) for chunk in received], [16000, 16000, 3200])
        self.assertFalse(received[0].startswith(b"RIFF"))
        latency = client_latencies(output["timing"])
        self.assertGreaterEqual(latency["time_to_first_transcript_seconds"], .95)
        self.assertAlmostEqual(latency["time_to_first_transcript_seconds"], output["event_arrivals_seconds"][1])
        self.assertTrue(latency["first_transcript_before_last_audio"])

    async def test_completed_run_calculates_rtf_and_missing_reference(self):
        class Monitor:
            def __init__(self, *args): pass
            def start(self): pass
            def stop(self):
                return {"errors": [], "samples": [], "summary": aggregate_resources([])}

        async def completed_stream(*args, observation, **kwargs):
            events = [
                {"type": "segment_transcript", "stream_id": "mock", "segment_index": 0, "language": "English",
                 "text": "Hello.", "audio_start_seconds": 0, "audio_end_seconds": 3,
                 "inference_time_seconds": .2, "queue_wait_time_seconds": .1},
                {"type": "segment_transcript", "stream_id": "mock", "segment_index": 1, "language": "English",
                 "text": "World.", "audio_start_seconds": 3, "audio_end_seconds": 3.5,
                 "inference_time_seconds": .3, "queue_wait_time_seconds": .1},
                {"type": "final_transcript", "stream_id": "mock", "language": "English", "text": "Hello. World."},
                {"type": "metrics", "stream_id": "mock", "total_audio_duration_seconds": 3.5,
                 "total_inference_time_seconds": .5, "total_queue_wait_time_seconds": .2},
                {"type": "completed", "stream_id": "mock"},
            ]
            observation.update(events=events, timing={"stream_started_at": 10, "last_audio_sent_at": 13.5,
                "end_sent_at": 13.5, "first_transcript_at": 13.2, "final_transcript_at": 14, "completed_at": 14.1})
            return observation

        sample = {"language": "English", "audio": "synthetic.wav", "format_status": "PASS", "duration_seconds": 3.5}
        result = await benchmark_run(sample, 1, "ws://test", 1, .5, 10, monitor_factory=Monitor, stream=completed_stream)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertAlmostEqual(result["inference_rtf"], .5/3.5)
        self.assertEqual(result["accuracy"]["status"], "NOT MEASURED")
        self.assertEqual(result["transcript"], "Hello. World.")
        sample.update(reference_text="Hello world", reference_verified=True)
        measured = await benchmark_run(sample, 2, "ws://test", 1, .5, 10, monitor_factory=Monitor, stream=completed_stream)
        self.assertEqual(measured["accuracy"]["error_rate"], 0)
        summary = summarize([result, measured])[0]
        self.assertEqual(summary["completed_runs"], 2)
        self.assertEqual(summary["time_to_first_transcript_seconds"]["count"], 2)
        self.assertEqual(summary["mean_error_rate"]["count"], 1)
        with tempfile.TemporaryDirectory() as directory:
            save_results({"created_at": "test", "system": {}, "configuration": {}, "runs": [result, measured],
                          "warmups": [], "setup_errors": []}, Path(directory))
            with (Path(directory) / "window_metrics.csv").open(encoding="utf-8-sig") as source:
                self.assertEqual(len(list(csv.DictReader(source))), 4)

    async def test_timeout_records_failure_and_stops_monitor(self):
        class Monitor:
            stopped = False
            def __init__(self, *args): pass
            def start(self): pass
            def stop(self):
                Monitor.stopped = True
                return {"errors": [], "samples": [], "summary": aggregate_resources([])}

        async def stalled(*args, **kwargs):
            await asyncio.sleep(1)

        sample = {"language": "English", "audio": "synthetic.wav", "format_status": "PASS", "duration_seconds": 1}
        result = await benchmark_run(sample, 1, "ws://test", 1, .5, .01, monitor_factory=Monitor, stream=stalled)
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("TimeoutError", result["errors"][0])
        self.assertTrue(Monitor.stopped)


if __name__ == "__main__":
    unittest.main(verbosity=2)
