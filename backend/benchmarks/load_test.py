"""Guarded concurrent call-leg tests using the existing CPU WebSocket client."""

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime
import math
from pathlib import Path
import sys
from time import perf_counter
from urllib.parse import urlsplit

import httpx
import psutil

from backend.benchmarks.benchmark import local_backend_process, system_metadata
from backend.benchmarks.benchmark_metrics import ResourceMonitor, client_latencies
from backend.benchmarks.load_test_metrics import Limits, aggregate_level, assess_round, memory_violation, select_capacity
from backend.benchmarks.load_test_report import save_load_results
from backend.config import Language
from backend.test_streaming_client import stream_file
from backend.validate_multilingual import check_events
from backend.validate_samples import ROOT, inspect_sample, load_manifest


def now():
    return datetime.now().astimezone().isoformat()


def backend_configuration(system, process):
    env = process.environ()
    return {"max_sessions": system.get("max_streams", int(env.get("ASR_MAX_STREAMS", "2"))),
            "pending_windows_per_session": system.get("pending_windows", int(env.get("ASR_PENDING_WINDOWS", "4"))),
            "window_ms": system.get("window_ms", int(env.get("ASR_WINDOW_MS", "3000"))),
            "streaming_executor_workers": 1, "maximum_shared_model_inferences": 1,
            "source": "Live diagnostics when exposed; otherwise backend environment/source defaults. Executor and lock verified in source.",
            "queue_depth_observation": "Client-derived unfinished-window proxy; exact live server queue depth is unavailable."}


async def call_leg(index, sample, concurrency, run, url, gate, timeout, *, stream=stream_file):
    observation = {}
    row = {"call_leg": index, "concurrency": concurrency, "run": run, "language": sample["language"],
           "audio_duration_seconds": sample["duration_seconds"], "status": "FAILED", "errors": []}
    # Expose the same observation object to the round watchdog, including partial sends.
    row["observation"] = observation
    return row, asyncio.create_task(_perform_call(row, sample, url, gate, timeout, stream), name=f"call-leg-{index}")


async def _perform_call(row, sample, url, gate, timeout, stream):
    try:
        async with asyncio.timeout(timeout):
            output = await stream(ROOT / sample["audio"], sample["language"], 500, url,
                                  observation=row["observation"], verbose=False, start_event=gate)
        checked = check_events(output["events"], sample["language"], sample["duration_seconds"])
        if checked["stream_id"] != output.get("ready", {}).get("stream_id"):
            raise ValueError("Transcript events do not belong to this connection's ready stream ID.")
        windows = checked["segments"]
        if any(not math.isfinite(w[k]) or w[k] < 0 for w in windows for k in ("inference_time_seconds", "queue_wait_time_seconds")):
            raise ValueError("Invalid inference or queue time.")
        row.update(status="COMPLETED", stream_id=checked["stream_id"], transcript=checked["actual_transcript"],
                   client_latency=client_latencies(output["timing"]), windows=windows,
                   inference_seconds=sum(w["inference_time_seconds"] for w in windows),
                   queue_wait_seconds=sum(w["queue_wait_time_seconds"] for w in windows))
    except asyncio.CancelledError:
        row.update(status="CANCELLED")
        row["errors"].append("Cancelled by operator or resource guard; not a completed stream.")
    except TimeoutError:
        row["status"] = "TIMED_OUT"
        row["errors"].append(f"TimeoutError: Session exceeded {timeout} seconds.")
    except Exception as exc:
        row["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        observation = row["observation"]
        row.update(connection_success=observation.get("connection_success", False),
                   bytes_sent=observation.get("bytes_sent", 0), chunks_sent=observation.get("chunks_sent", 0))
        row.setdefault("stream_id", observation.get("ready", {}).get("stream_id"))
        events = [e for e in observation.get("events", []) if isinstance(e, dict)]
        finals = [e for e in events if e.get("type") == "final_transcript"]
        row.update(session_ready=bool(observation.get("ready")), final_transcript_received=bool(finals),
                   final_transcript=finals[-1].get("text") if finals else None)
        row["observed_windows"] = [e for e in events if e.get("type") == "segment_transcript"]
        # Partial first-text/final timings remain useful when a session later fails.
        if "client_latency" not in row:
            timing = observation.get("timing", {})
            start = timing.get("stream_started_at")
            first, end, final = (timing.get(k) for k in ("first_transcript_at", "end_sent_at", "final_transcript_at"))
            last, completed = timing.get("last_audio_sent_at"), timing.get("completed_at")
            row["client_latency"] = {
                "time_to_first_transcript_seconds": first - start if first is not None and start is not None else None,
                "first_transcript_before_last_audio": first < last if first is not None and last is not None else None,
                "finalization_from_end_seconds": final - end if final is not None and end is not None else None,
                "finalization_from_last_audio_seconds": final - last if final is not None and last is not None else None,
                "total_end_to_end_seconds": completed - start if completed is not None and start is not None else None}


async def run_round(sample, concurrency, run, url, pid, limits, interval, timeout, window_ms,
                    *, monitor_factory=ResourceMonitor, stream=stream_file, cleanup_grace=3):
    started = perf_counter()
    result = {"concurrency": concurrency, "run": run, "started_at": now(), "calls": [],
              "guardrail_reasons": [], "backlog_samples": [], "queue_growth_detected": False}
    gate = asyncio.Event()
    monitor = monitor_factory(pid, interval)
    tasks = []
    guard = None

    async def watchdog():
        observed_count, cpu_seconds = 0, 0.0
        over_depth_since = {}
        while True:
            await asyncio.sleep(interval)
            if monitor.errors:
                return "Resource monitoring failed: " + "; ".join(monitor.errors)
            for point in monitor.samples[observed_count:]:
                violation = memory_violation(point, limits)
                if violation:
                    return violation
                cpu = point.get("system_cpu_percent")
                cpu_seconds = cpu_seconds + point.get("interval_seconds", 0) if cpu is not None and cpu >= limits.cpu_percent else 0
                if cpu_seconds >= limits.sustained_seconds:
                    return f"System CPU >= {limits.cpu_percent}% for {cpu_seconds:.2f}s."
            observed_count = len(monitor.samples)
            for call in result["calls"]:
                observation = call["observation"]
                frames = observation.get("bytes_sent", 0) // 2
                produced = frames // (16 * window_ms)
                if observation.get("timing", {}).get("end_sent_at") is not None and frames % (16 * window_ms):
                    produced += 1
                received = sum(e.get("type") == "segment_transcript" for e in observation.get("events", []))
                unfinished = max(0, produced - received)
                # One window may be executing or waiting in the executor outside
                # the per-session audio queue. This remains a client-side proxy.
                pending_proxy = max(0, unfinished - 1)
                result["backlog_samples"].append({"elapsed_seconds": perf_counter() - started,
                    "call_leg": call["call_leg"], "unfinished_windows_proxy": unfinished, "pending_windows_proxy": pending_proxy})
                if pending_proxy > limits.max_queue_depth:
                    over_depth_since.setdefault(call["call_leg"], perf_counter())
                    if perf_counter() - over_depth_since[call["call_leg"]] >= limits.sustained_seconds:
                        result["queue_growth_detected"] = True
                        return "Client-derived pending-window proxy exceeded the configured bound persistently."
                else:
                    over_depth_since.pop(call["call_leg"], None)

    try:
        monitor.start()
        violation = memory_violation(monitor.samples[0], limits)
        if violation:
            # No WebSockets were attempted: caller records this as a preflight
            # refusal, not as a measured load round.
            result["preflight_refusal"] = violation
            return result
        for index in range(1, concurrency + 1):
            row, task = await call_leg(index, sample, concurrency, run, url, gate, timeout, stream=stream)
            result["calls"].append(row)
            tasks.append(task)
        await asyncio.sleep(0)  # Let clients finish validation and reach the gate.
        gate.set()
        guard = asyncio.create_task(watchdog(), name="load-test-guard")
        remaining = set(tasks)
        while remaining:
            done, _ = await asyncio.wait(remaining | {guard}, return_when=asyncio.FIRST_COMPLETED)
            if guard in done:
                result["guardrail_reasons"].append(guard.result())
                for task in remaining:
                    task.cancel()
                break
            remaining.difference_update(done)
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        result["guardrail_reasons"].append("Operator cancelled the load test.")
        result["operator_cancelled"] = True
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if guard is not None:
            guard.cancel()
            await asyncio.gather(guard, return_exceptions=True)
        failed = any(c["status"] != "COMPLETED" for c in result["calls"])
        if failed and cleanup_grace:
            # The backend owns any uninterruptible CPU operation. Retain resource
            # observations briefly and never start another level after failure.
            await asyncio.sleep(cleanup_grace)
        result["resources"] = monitor.stop()
        for point in result["resources"]["samples"]:
            if violation := memory_violation(point, limits):
                result["guardrail_reasons"].append(violation)
        result["guardrail_reasons"] = list(dict.fromkeys(result["guardrail_reasons"]))
        result["elapsed_seconds"] = perf_counter() - started
    if result.get("preflight_refusal"):
        return result
    ids = [c["stream_id"] for c in result["calls"] if c.get("stream_id")]
    if len(set(ids)) != len(ids):
        for c in result["calls"]:
            c.update(status="FAILED")
            c["errors"].append("Duplicate stream ID across independent clients.")
    attempts = [c["observation"]["timing"]["connection_attempt_at"] for c in result["calls"]
                if "connection_attempt_at" in c["observation"].get("timing", {})]
    result["connection_start_spread_seconds"] = max(attempts) - min(attempts) if attempts else None
    completed = [c for c in result["calls"] if c["status"] == "COMPLETED"]
    result["inference_demand_ratio"] = (sum(c["inference_seconds"] for c in completed) / sample["duration_seconds"]
                                        if len(completed) == concurrency else None)
    # None indicates no complete workload observation, not zero processing demand.
    result["acceptance"] = assess_round(result, limits)
    return result


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    levels = cli.add_mutually_exclusive_group()
    levels.add_argument("--concurrency", type=int)
    levels.add_argument("--levels", type=int, nargs="+", help="Ascending levels; default: 1 2 3 4.")
    cli.add_argument("--audio", type=Path, help="Default: selected language's manifest recording.")
    cli.add_argument("--language", choices=[l.value for l in Language], default="English")
    cli.add_argument("--runs", type=int, default=3)
    cli.add_argument("--ws-url", default="ws://127.0.0.1:8000/ws/transcribe")
    cli.add_argument("--api-base-url")
    cli.add_argument("--backend-pid", type=int)
    cli.add_argument("--monitor-interval", type=float, default=.5)
    cli.add_argument("--timeout", type=float, default=120)
    cli.add_argument("--max-clients", type=int, default=4)
    cli.add_argument("--cpu-threshold", type=float, default=90)
    cli.add_argument("--ram-threshold", type=float, default=85)
    cli.add_argument("--sustained-seconds", type=float, default=3)
    cli.add_argument("--min-available-mib", type=float, default=1024)
    cli.add_argument("--finalization-target", type=float, default=5)
    cli.add_argument("--max-queue-wait", type=float, default=6)
    cli.add_argument("--max-queue-depth", type=int, default=4)
    cli.add_argument("--inference-demand-limit", type=float, default=1)
    cli.add_argument("--pause-between-levels", action="store_true", help="Ask in the terminal before proceeding to the next level.")
    cli.add_argument("--output", type=Path, default=ROOT / "benchmark_results")
    return cli


async def execute(args):
    limits = Limits(args.cpu_threshold, args.ram_threshold, args.sustained_seconds,
                    args.min_available_mib, args.finalization_target, args.max_queue_wait,
                    args.max_queue_depth, args.inference_demand_limit)
    levels = [args.concurrency] if args.concurrency else (args.levels or [1, 2, 3, 4])
    if args.audio:
        entry = {"language": args.language, "audio": str(args.audio), "reference": "", "reference_verified": False}
    else:
        entry = next(e for e in load_manifest() if e["language"] == args.language)
    sample = inspect_sample(entry)
    if sample["format_status"] != "PASS":
        raise ValueError("Audio validation failed: " + "; ".join(sample["errors"]))
    address = urlsplit(args.ws_url)
    api = args.api_base_url or f"{'https' if address.scheme == 'wss' else 'http'}://{address.netloc}"
    async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
        health_response = await client.get(api.rstrip("/") + "/health")
        health_response.raise_for_status()
        health = health_response.json()
        if health.get("model_loaded") is not True or health.get("device") != "cpu" or health.get("model") != "Qwen3-ASR-0.6B":
            raise ValueError("Existing Qwen3-ASR-0.6B CPU model must be ready.")
        response = await client.get(api.rstrip("/") + "/api/system")
        response.raise_for_status()
        system = response.json()
    process = local_backend_process(args.ws_url, system, args.backend_pid)
    result = {"schema_version": 1, "recorded_at": now(), "settings": {**vars(args), "limits": asdict(limits)},
              "system": system_metadata(system, process), "backend_configuration": backend_configuration(system, process),
              "sample": sample, "rounds": [], "levels": [], "stopped_reason": None}
    print(f"Model ready. Backend PID {process.pid}; admission limit {result['backend_configuration']['max_sessions']}; one model inference at a time.")
    for level in levels:
        for run in range(1, args.runs + 1):
            print(f"Concurrency {level}, run {run}/{args.runs}...", flush=True)
            measured = await run_round(sample, level, run, args.ws_url, process.pid, limits,
                                       args.monitor_interval, args.timeout, result["backend_configuration"]["window_ms"])
            if measured.get("preflight_refusal"):
                result["stopped_reason"] = "Preflight: " + measured["preflight_refusal"]
                break
            result["rounds"].append(measured)
            result["levels"] = [aggregate_level([r for r in result["rounds"] if r["concurrency"] == n], args.runs)
                                for n in dict.fromkeys(r["concurrency"] for r in result["rounds"])]
            result["capacity"] = select_capacity(result["levels"])
            save_load_results(result, args.output)
            print(f"  completed {sum(c['status'] == 'COMPLETED' for c in measured['calls'])}/{level}; "
                  f"criteria: {'PASS' if measured['acceptance']['meets_criteria'] else 'FAIL'}", flush=True)
            if not measured["acceptance"]["meets_criteria"]:
                result["stopped_reason"] = "; ".join(measured["acceptance"]["reasons"])
                break
        if result["stopped_reason"]:
            break
        if args.pause_between_levels and level != levels[-1]:
            # All clients/monitors are closed here. Plain terminal input avoids
            # leaving a blocking input thread behind on Ctrl+C.
            answer = input("Continue to the next level? [y/N] ")
            if answer.strip().lower() != "y":
                result["stopped_reason"] = "Operator stopped after the completed level."
                break
    result["capacity"] = select_capacity(result["levels"])
    result["untested_levels"] = [n for n in levels if not any(r["concurrency"] == n for r in result["rounds"])]
    if result["rounds"]:
        save_load_results(result, args.output)
        print(f"Measured observations saved to {args.output}")
    else:
        print("No streams attempted; no benchmark result files written.")
    print(result["stopped_reason"] or "Requested sequence finished.")
    return result


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cli = parser()
    args = cli.parse_args()
    levels = [args.concurrency] if args.concurrency is not None else (args.levels or [1, 2, 3, 4])
    if (args.runs < 1 or args.max_clients < 1 or levels != sorted(set(levels)) or
            any(n < 1 or n > args.max_clients for n in levels) or
            not math.isfinite(args.monitor_interval) or args.monitor_interval < .1 or
            not math.isfinite(args.timeout) or args.timeout <= 0):
        cli.error("Use ascending unique positive levels within --max-clients, runs >= 1, interval >= .1, and a positive timeout.")
    try:
        result = asyncio.run(execute(args))
        return int(bool(result["stopped_reason"]))
    except KeyboardInterrupt:
        print("Cancelled. Previously saved observations remain available.")
        return 130
    except Exception as exc:
        print(f"Load test did not start: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
