"""Sequential WebSocket benchmarks: python -m backend.benchmarks.benchmark."""

import argparse
import asyncio
from datetime import datetime
import importlib.metadata
import math
from pathlib import Path
import platform
import sys
from urllib.parse import urlsplit

import httpx
import psutil

from backend.benchmarks.accuracy_metrics import score_accuracy
from backend.benchmarks.benchmark_metrics import ResourceMonitor, client_latencies, inference_rtf
from backend.benchmarks.benchmark_report import save_results, summarize
from backend.config import Language, Settings
from backend.test_streaming_client import stream_file
from backend.validate_multilingual import check_events
from backend.validate_samples import DEFAULT_MANIFEST, ROOT, inspect_sample, load_manifest


def local_backend_process(url: str, system: dict, requested_pid: int | None) -> psutil.Process:
    """Never label the benchmark client's own process as backend CPU/RAM."""
    address = urlsplit(url)
    if address.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Local psutil monitoring requires a loopback backend URL; remote monitoring is not supported.")
    pid = requested_pid or system.get("process_id")
    if pid is None:
        port = address.port or (443 if address.scheme == "wss" else 80)
        matches = {c.pid for c in psutil.net_connections(kind="tcp")
                   if c.status == psutil.CONN_LISTEN and c.laddr.port == port and c.pid}
        if len(matches) != 1:
            raise ValueError("Cannot identify one backend listener. Supply --backend-pid for the actual Uvicorn worker.")
        pid = matches.pop()
    process = psutil.Process(pid)
    command = " ".join(process.cmdline()).lower()
    if "uvicorn" not in command or "backend.main:app" not in command:
        raise ValueError("Selected PID is not the expected Uvicorn backend. For a Vite proxy use --backend-pid.")
    return process


def system_metadata(system: dict, process: psutil.Process) -> dict:
    """Read live diagnostics when available; label old-server fallbacks explicitly."""
    try:
        environment = process.environ()
        configured_threads = int(environment.get("ASR_CPU_THREADS", str(Settings().cpu_threads)))
        configured_window = int(environment.get("ASR_WINDOW_MS", "3000"))
        source = "Backend process environment or source defaults; live torch count unavailable on this already-running server"
    except (psutil.Error, ValueError):
        configured_threads, configured_window, source = None, None, "NOT MEASURED"
    cpu_model = platform.processor()
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                cpu_model = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    versions = {}
    for package in ("torch", "qwen-asr", "psutil", "jiwer", "websockets"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {"cpu_model": cpu_model, "operating_system": system.get("operating_system", platform.platform()),
            "cpu_physical_cores": system["cpu_physical_cores"], "cpu_logical_processors": system["cpu_logical_processors"],
            "total_ram_bytes": system["total_ram_bytes"], "python_version": system["python_version"],
            "model": system["model"], "device": system["device"], "runtime": "PyTorch CPU / Transformers",
            "backend_pid": process.pid, "backend_process_created_at": process.create_time(),
            "backend_command": process.cmdline(), "package_versions_local_environment": versions,
            "configured_cpu_threads": system.get("configured_cpu_threads", configured_threads),
            "thread_configuration_source": "Live /api/system" if "configured_cpu_threads" in system else source,
            "torch_num_threads": system.get("torch_num_threads"), "torch_version_live": system.get("torch_version"),
            "window_ms": system.get("window_ms", configured_window), "chunk_duration_ms": 500,
            "model_load_time_seconds": system.get("model_load_time_seconds"),
            "model_loading_note": "Separate cached startup load; NOT MEASURED if null. Existing server is never restarted by this runner.",
            "system_endpoint": system}


async def benchmark_run(sample: dict, run_index: int, url: str, pid: int, interval: float,
                        timeout: float, *, monitor_factory=ResourceMonitor, stream=stream_file) -> dict:
    row = {"language": sample["language"], "audio": sample["audio"], "run": run_index,
           "started_at": datetime.now().astimezone().isoformat(), "status": "FAILED", "errors": [],
           "audio_duration_seconds": sample.get("duration_seconds"), "sample": sample}
    observation = {}
    monitor = None
    try:
        if sample["format_status"] != "PASS":
            raise ValueError("Invalid/missing audio: " + "; ".join(sample["errors"]))
        monitor = monitor_factory(pid, interval)
        monitor.start()
        async with asyncio.timeout(timeout):
            output = await stream(ROOT / sample["audio"], sample["language"], 500, url,
                                  observation=observation, verbose=False)
        checked = check_events(output["events"], sample["language"], sample["duration_seconds"])
        timings = client_latencies(output["timing"])
        segments = checked["segments"]
        inference = sum(s["inference_time_seconds"] for s in segments)
        queue = sum(s["queue_wait_time_seconds"] for s in segments)
        if any(not math.isfinite(s[key]) or s[key] < 0 for s in segments
               for key in ("inference_time_seconds", "queue_wait_time_seconds")):
            raise ValueError("Backend returned invalid inference/queue durations.")
        if not math.isclose(inference, checked["metrics"]["total_inference_time_seconds"], abs_tol=0.000001):
            raise ValueError("Backend inference total differs from its windows.")
        if not math.isclose(queue, checked["metrics"]["total_queue_wait_time_seconds"], abs_tol=0.000001):
            raise ValueError("Backend queue total differs from its windows.")
        row.update(status="COMPLETED", transcript=checked["actual_transcript"], stream_id=checked["stream_id"],
                   segment_count=checked["segment_count"], windows=segments, client_latency=timings,
                   server_metrics=checked["metrics"], inference_seconds=inference, queue_wait_seconds=queue,
                   inference_rtf=inference_rtf(inference, sample["duration_seconds"]),
                   accuracy=score_accuracy(sample["language"], sample.get("reference_text"), checked["actual_transcript"],
                                           verified=sample.get("reference_verified") is True))
    except asyncio.CancelledError:
        row["errors"].append("Benchmark cancelled; this is not a completed run.")
        raise
    except Exception as exc:
        row["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        row["client_observation"] = observation
        if monitor is not None:
            row["resources"] = monitor.stop()
            if row["resources"]["errors"]:
                row["errors"].extend(row["resources"]["errors"])
                row["status"] = "FAILED"
    return row


async def run_benchmarks(args) -> dict:
    entries = load_manifest(args.manifest) if args.all else [{
        "language": args.language, "ui_label": args.language, "audio": str(args.audio),
        "reference": str(args.reference) if args.reference else "",
        "reference_verified": args.reference_verified}]
    samples = [inspect_sample(entry) for entry in entries]
    address = urlsplit(args.url)
    api = args.api_base_url or f"{'https' if address.scheme == 'wss' else 'http'}://{address.netloc}"
    result = {"schema_version": 1, "created_at": datetime.now().astimezone().isoformat(),
              "configuration": {"websocket_url": args.url, "api_base_url": api,
                                "measured_runs_per_language": args.runs, "warmup_per_language": args.warmup,
                                "monitor_interval_seconds": args.monitor_interval,
                                "timeout_per_stream_seconds": args.timeout, "manifest": str(args.manifest) if args.all else None},
              "system": {}, "runs": [], "warmups": [], "setup_errors": []}
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
            health_response = await client.get(api.rstrip("/") + "/health")
            health_response.raise_for_status()
            health = health_response.json()
            if health.get("model_loaded") is not True or health.get("device") != "cpu" or health.get("model") != "Qwen3-ASR-0.6B":
                raise ValueError("The existing Qwen3-ASR-0.6B CPU backend must be loaded before benchmarking.")
            response = await client.get(api.rstrip("/") + "/api/system")
            response.raise_for_status()
            system = response.json()
        process = local_backend_process(args.url, system, args.backend_pid)
        result["system"] = system_metadata(system, process)
        result["health"] = health
    except Exception as exc:
        result["setup_errors"].append(f"{type(exc).__name__}: {exc}")
        return result
    for sample in samples:
        phases = [("warmups", 0)] if args.warmup else []
        phases += [("runs", i) for i in range(1, args.runs + 1)]
        for phase, index in phases:
            print(f"{sample['language']} {'warm-up' if phase == 'warmups' else f'run {index}/{args.runs}'}...", flush=True)
            row = await benchmark_run(sample, index, args.url, process.pid, args.monitor_interval, args.timeout)
            result[phase].append(row)
            if row["status"] == "COMPLETED":
                latency, accuracy = row["client_latency"], row["accuracy"]
                error_rate = accuracy["error_rate"]
                score = "NOT MEASURED" if error_rate is None else f"{100 * error_rate:.2f}%"
                print(f"  completed: first={latency['time_to_first_transcript_seconds']}s; "
                      f"RTF={row['inference_rtf']:.3f}; {accuracy['metric']}={score}", flush=True)
            else:
                print("  FAILED: " + "; ".join(row["errors"]), flush=True)
            result["summary"] = summarize(result["runs"])
            save_results(result, args.output_dir)  # Preserve finished runs if a later run fails.
            if row["status"] != "COMPLETED":
                # A disconnected synchronous inference may still be finishing.
                # Do not open another stream while its cleanup status is unknown.
                result["aborted_after_failure"] = True
                return result
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", help="Use all three manifest entries.")
    selection.add_argument("--language", choices=[x.value for x in Language])
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-verified", action="store_true", help="Assert you checked this reference against the recording.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--warmup", action="store_true", help="One separate, unmeasured-in-summary warm-up stream per language.")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/transcribe")
    parser.add_argument("--api-base-url")
    parser.add_argument("--backend-pid", type=int, help="Actual local Uvicorn worker; useful through a Vite proxy.")
    parser.add_argument("--monitor-interval", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "benchmark_results")
    args = parser.parse_args()
    if args.runs < 1 or not math.isfinite(args.monitor_interval) or args.monitor_interval < 0.1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("Use runs >= 1, monitor interval >= 0.1 seconds, and a positive timeout.")
    if args.language and not args.audio:
        parser.error("--language requires --audio.")
    if args.all and (args.audio or args.reference or args.reference_verified):
        parser.error("With --all, audio/reference verification comes from the manifest.")
    try:
        result = asyncio.run(run_benchmarks(args))
    except KeyboardInterrupt:
        print("Cancelled; already saved completed runs are retained.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Invalid manifest and other setup failures still get an explicit artifact.
        result = {"schema_version": 1, "created_at": datetime.now().astimezone().isoformat(),
                  "configuration": {}, "system": {}, "runs": [], "warmups": [],
                  "setup_errors": [f"{type(exc).__name__}: {exc}"]}
    result["summary"] = summarize(result["runs"])
    save_results(result, args.output_dir)
    print(f"Results: {args.output_dir.resolve()}")
    for error in result["setup_errors"]:
        print(error, file=sys.stderr)
    return int(bool(result["setup_errors"]) or any(row["status"] != "COMPLETED" for row in result["runs"] + result["warmups"]))


if __name__ == "__main__":
    raise SystemExit(main())
