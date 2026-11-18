"""Export real concurrency observations; never create sample result rows."""

import csv
import io
import json
from pathlib import Path


def csv_text(rows):
    buffer = io.StringIO(newline="")
    fields = list(dict.fromkeys(k for row in rows for k in row))
    if fields:
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return buffer.getvalue()


def number(value, percent=False):
    return "Unavailable" if value is None else f"{value * (100 if percent else 1):.3f}" + ("%" if percent else "")


def report(result):
    config = result["backend_configuration"]
    lines = ["# Measured concurrency results", "", f"Recorded: {result['recorded_at']}", "",
             f"Audio: `{result['sample']['audio']}` ({result['sample']['language']}, {result['sample']['duration_seconds']:.3f} seconds).",
             "", f"Hardware: {result['system']['cpu_model']}; {result['system']['cpu_physical_cores']} physical / "
             f"{result['system']['cpu_logical_processors']} logical processors; {result['system']['total_ram_bytes']/1024**3:.3f} GiB usable RAM.",
             f"OS: {result['system']['operating_system']}; Python {result['system']['python_version']}; model {result['system']['model']} on CPU.",
             "", f"Backend admission limit: {config['max_sessions']}; pending audio windows per session: {config['pending_windows_per_session']}; "
             f"inference operations at once: {config['maximum_shared_model_inferences']}; window: {config['window_ms']} ms.",
             f"Configured torch threads: {result['system']['configured_cpu_threads']} ({result['system']['thread_configuration_source']}).",
             "", "One call leg is one WebSocket session. All sessions share one serialized model. A connected socket is not evidence of sustainable real-time processing.",
             "", "## Criteria", "", f"`{result['settings']['limits']}`", "",
             "Targets are configurable choices, not an assignment SLA. Acceptance requires every call to complete in every requested repetition, "
             "valid separate stream IDs/events, bounded observed waiting, an adequate serialized inference-time budget, and CPU/RAM within guardrails.",
             "", "## Level summaries", "",
             "Latency percentiles pool completed calls across repetitions. Failed/cancelled calls are counted separately and are not assigned fabricated latency values.",
             "", "| Level | Runs | Complete / requested | Success | First text mean / P50 / P95 / max s | Finalization mean / P50 / P95 / max s | Meets criteria |",
             "|---|---|---|---|---|---|---|"]
    for level in result["levels"]:
        def latency(key):
            return " / ".join(number(level[key][x]) for x in ("mean", "p50", "p95", "max"))
        lines.append(f"| {level['concurrency']} | {level['executed_runs']}/{level['required_runs']} | "
                     f"{level['completed_streams']}/{level['requested_streams']} | {number(level['success_rate'], True)} | "
                     f"{latency('time_to_first_transcript_seconds')} | {latency('finalization_from_end_seconds')} | {level['meets_criteria']} |")
    lines += ["", "## CPU, memory and queue observations", "",
              "| Level | System CPU mean / peak % | Backend CPU mean / peak % | Peak RSS MiB | Queue wait mean / max s | Test duration s |",
              "|---|---|---|---:|---|---:|"]
    for level in result["levels"]:
        resources = level["resources"]
        cpu = lambda name: f"{number(resources[name]['mean'])} / {number(resources[name]['peak'])}"
        queue = level["window_queue_wait_seconds"]
        lines.append(f"| {level['concurrency']} | {cpu('system_cpu_percent')} | {cpu('process_cpu_percent')} | "
                     f"{number(resources['process_rss_mib']['max'])} | {number(queue['mean'])} / {number(queue['max'])} | {number(level['total_test_seconds'])} |")
    lines += ["", "System CPU includes all programs, including this client. Backend CPU uses 100% per logical processor and may exceed 100%. "
              "RSS covers the entire backend, excludes children, and is not model-weight-only memory. CPU averages are weighted by sampling intervals; peaks are observed samples.",
              "", "## Per-call outcomes", "", "| Level/run/call | Stream ID | Status | Bytes/chunks | First s | Finalization s | End-to-end s | Error |",
              "|---|---|---|---|---:|---:|---:|---|"]
    for r in result["rounds"]:
        for c in r["calls"]:
            lat = c.get("client_latency", {})
            errors = "; ".join(c["errors"]).replace("|", "/").replace("\n", " ")
            lines.append(f"| {r['concurrency']}/{r['run']}/{c['call_leg']} | {c.get('stream_id') or 'Unavailable'} | {c['status']} | "
                         f"{c['bytes_sent']}/{c['chunks_sent']} | {number(lat.get('time_to_first_transcript_seconds'))} | "
                         f"{number(lat.get('finalization_from_end_seconds'))} | {number(lat.get('total_end_to_end_seconds'))} | {errors} |")
    lines += ["", "## Memory before and after", "", "| Level/run | Before RSS MiB | After RSS MiB | Minimum available system MiB |",
              "|---|---:|---:|---:|"]
    for r in result["rounds"]:
        samples = r["resources"]["samples"]
        before = next((s["process_rss_bytes"]/1024**2 for s in samples if s["stage"] == "before"), None)
        after = next((s["process_rss_bytes"]/1024**2 for s in samples if s["stage"] == "after"), None)
        lines.append(f"| {r['concurrency']}/{r['run']} | {number(before)} | {number(after)} | {number(r['resources']['summary']['available_ram_mib']['min'])} |")
    capacity = result.get("capacity", {})
    lines += ["", "## Capacity conclusion", "",
              f"- Highest level attempted: {capacity.get('highest_attempted')}",
              f"- Highest fully successful repeated level: {capacity.get('highest_successful')}",
              f"- Highest meeting short-recording criteria: {capacity.get('highest_meeting_criteria')}",
              f"- Maximum established: {capacity.get('maximum_established', False)}",
              f"- Untested requested levels: {result.get('untested_levels', [])}",
              f"- Stopping reason: {result.get('stopped_reason') or 'Requested sequence finished.'}", "",
              "Inference is serialized: competing windows wait for the same execution slot, even when overall system CPU is below 100%. "
              "Extra streams do not create extra model instances. Per-session queues/buffers add memory; model weights are shared.",
              "", "Exact live queue depth is unavailable on this server. Raw client unfinished-window proxies and real backend per-window wait times "
              "are saved separately; proxies are not relabeled as exact queue measurements. The sum of inference seconds divided by one audio duration "
              "tests whether the serialized model can supply the offered audio-time workload; short recordings still cannot prove indefinite stability.",
              "", "Small samples, fixed language/content, Windows paging, laptop power/thermal changes, and uncontrolled background tasks limit conclusions. "
              "Admission rejection identifies a software cap, not a hardware maximum. Cancelled synchronous model work can finish safely on the server after clients close. "
              "No server capacity equivalence or production sizing is proven by these measurements.", ""]
    return "\n".join(lines)


def save_load_results(result, directory: Path):
    if not result["rounds"]:
        return  # Do not manufacture result files for a workload never attempted.
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "concurrency_results.json.tmp"
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(directory / "concurrency_results.json")
    rows = []
    for r in result["rounds"]:
        for c in r["calls"]:
            rows.append({"concurrency": r["concurrency"], "run": r["run"], "call_leg": c["call_leg"], "language": c["language"],
                         "stream_id": c.get("stream_id"), "status": c["status"], "connection_success": c["connection_success"],
                         "session_ready": c.get("session_ready", bool(c.get("observation", {}).get("ready"))),
                         "final_transcript_received": c.get("final_transcript_received", any(
                             e.get("type") == "final_transcript" for e in c.get("observation", {}).get("events", []) if isinstance(e, dict))),
                         "audio_duration_seconds": c["audio_duration_seconds"], "bytes_sent": c["bytes_sent"], "chunks_sent": c["chunks_sent"],
                         **c.get("client_latency", {}), "inference_seconds": c.get("inference_seconds"),
                         "queue_wait_seconds": c.get("queue_wait_seconds"), "transcript": c.get("transcript", c.get("final_transcript")),
                         "errors": "; ".join(c["errors"]), "round_meets_criteria": r["acceptance"]["meets_criteria"]})
    (directory / "concurrency_results.csv").write_text(csv_text(rows), encoding="utf-8-sig")
    (directory / "concurrency_summary.md").write_text(report(result), encoding="utf-8")
