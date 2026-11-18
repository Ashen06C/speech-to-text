"""Export measured runs to JSON, CSV, and Markdown without placeholder data."""

import csv
import io
import json
from pathlib import Path

from backend.benchmarks.benchmark_metrics import MIB, describe


def summarize(runs: list[dict]) -> list[dict]:
    summaries = []
    for language in dict.fromkeys(row["language"] for row in runs):
        language_runs = [r for r in runs if r["language"] == language]
        valid = [r for r in language_runs if r["status"] == "COMPLETED"]
        summary = {"language": language, "completed_runs": len(valid), "failed_runs": len(language_runs) - len(valid)}
        for key in ("time_to_first_transcript_seconds", "finalization_from_end_seconds", "total_end_to_end_seconds"):
            summary[key] = describe([r["client_latency"][key] for r in valid if r["client_latency"][key] is not None])
        for key in ("inference_seconds", "queue_wait_seconds", "inference_rtf"):
            summary[key] = describe([r[key] for r in valid])
        for resource, label in (("system_cpu_percent", "system_cpu"), ("process_cpu_percent", "process_cpu")):
            summary[f"{label}_run_means_percent"] = describe([r["resources"]["summary"][resource]["mean"] for r in valid
                                                              if r["resources"]["summary"][resource]["mean"] is not None])
            summary[f"{label}_peak_percent"] = max((r["resources"]["summary"][resource]["peak"] for r in valid
                                                     if r["resources"]["summary"][resource]["peak"] is not None), default=None)
        summary["peak_process_rss_mib"] = max((r["resources"]["summary"]["process_rss_mib"]["max"] for r in valid
                                                if r["resources"]["summary"]["process_rss_mib"]["max"] is not None), default=None)
        summary["mean_error_rate"] = describe([r["accuracy"]["error_rate"] for r in valid if r["accuracy"]["error_rate"] is not None])
        summary["error_metric"] = "CER" if language == "Chinese" else "WER"
        summary["progressive_runs"] = sum(r["client_latency"]["first_transcript_before_last_audio"] is True for r in valid)
        summaries.append(summary)
    return summaries


def csv_row(row: dict) -> dict:
    accuracy = row.get("accuracy", {})
    resource = row.get("resources", {}).get("summary", {})
    return {"language": row["language"], "audio": row["audio"], "run": row["run"], "status": row["status"],
            "audio_duration_seconds": row.get("audio_duration_seconds"), **row.get("client_latency", {}),
            "inference_seconds": row.get("inference_seconds"), "queue_wait_seconds": row.get("queue_wait_seconds"),
            "inference_rtf": row.get("inference_rtf"),
            "system_cpu_mean_percent": resource.get("system_cpu_percent", {}).get("mean"),
            "system_cpu_peak_percent": resource.get("system_cpu_percent", {}).get("peak"),
            "process_cpu_mean_percent": resource.get("process_cpu_percent", {}).get("mean"),
            "process_cpu_peak_percent": resource.get("process_cpu_percent", {}).get("peak"),
            "process_rss_mean_mib": resource.get("process_rss_mib", {}).get("mean"),
            "process_rss_peak_mib": resource.get("process_rss_mib", {}).get("max"),
            "accuracy_status": accuracy.get("status", "NOT MEASURED"), "error_metric": accuracy.get("metric"),
            "error_rate": accuracy.get("error_rate"),
            **{key: accuracy.get(key) for key in ("substitutions", "deletions", "insertions", "hits", "reference_units")},
            "reference": row.get("sample", {}).get("reference_text"), "transcript": row.get("transcript"),
            "errors": "; ".join(row["errors"])}


def _csv(rows: list[dict]) -> str:
    stream = io.StringIO(newline="")
    keys = list(dict.fromkeys(key for row in rows for key in row))
    if keys:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    return stream.getvalue()


def _number(value, percent=False):
    return "NOT MEASURED" if value is None else f"{value * (100 if percent else 1):.3f}" + ("%" if percent else "")


def _range(stats):
    return f"{_number(stats['mean'])} ({_number(stats['min'])}–{_number(stats['max'])})" if stats["count"] else "NOT MEASURED"


def render_report(result: dict) -> str:
    system, runs = result["system"], result["runs"]
    valid = [r for r in runs if r["status"] == "COMPLETED"]
    lines = ["# CPU WebSocket benchmark results", "", f"Recorded: {result['created_at']}", "",
             "Only executed runs appear below. Failed runs are retained and excluded from numerical summaries. "
             "Warm-ups, if requested, are stored separately in JSON and excluded from measured results.", "", "## System configuration", ""]
    for key in ("cpu_model", "cpu_physical_cores", "cpu_logical_processors", "operating_system", "python_version", "model", "device",
                "runtime", "backend_pid", "configured_cpu_threads", "torch_num_threads", "thread_configuration_source", "window_ms", "chunk_duration_ms", "model_load_time_seconds"):
        value = system.get(key)
        lines.append(f"- {key}: {value if value is not None else 'NOT MEASURED'}")
    lines += [f"- Total system RAM: {_number(system.get('total_ram_bytes', 0) / 1024**3) if system else 'NOT MEASURED'} GiB",
              f"- Local package versions: `{system.get('package_versions_local_environment', {})}`", "",
              "Model loading is separate from inference. An already-running older backend cannot report a historical load time or live torch thread count; null values remain NOT MEASURED. "
              "A configuration inferred from its environment/source is labeled, not claimed as a live torch measurement.", "", "## Individual measured runs", "",
              "CPU columns: interval-weighted mean / observed peak. Process CPU uses one logical CPU as 100% and may exceed 100%; system CPU is for the entire machine (0–100%). RSS excludes children.", "",
              "| Language/run | Audio s | First text s | End→final s | End-to-end s | Inference s | Queue s | RTF | System CPU % | Backend CPU % | RSS peak MiB | WER/CER |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in runs:
        if row["status"] != "COMPLETED":
            lines.append(f"| {row['language']}/{row['run']} FAILED | " + " | ".join(["—"] * 11) + " |")
            continue
        latency, resources, accuracy = row["client_latency"], row["resources"]["summary"], row["accuracy"]
        cpu = lambda kind: f"{_number(resources[kind]['mean'])} / {_number(resources[kind]['peak'])}"
        values = [row["audio_duration_seconds"], latency["time_to_first_transcript_seconds"], latency["finalization_from_end_seconds"],
                  latency["total_end_to_end_seconds"], row["inference_seconds"], row["queue_wait_seconds"], row["inference_rtf"]]
        lines.append(f"| {row['language']}/{row['run']} | " + " | ".join(_number(v) for v in values) +
                     f" | {cpu('system_cpu_percent')} | {cpu('process_cpu_percent')} | {_number(resources['process_rss_mib']['max'])} | "
                     f"{accuracy['metric']} {_number(accuracy['error_rate'], True)} |")
    lines += ["", "## Repeated-run summary", "", "Latency columns show mean (minimum–maximum); means across runs give each run equal weight.", "",
              "| Language | Completed/failed | First text s | End→final s | End-to-end s | Mean RTF | Mean system CPU % | Mean backend CPU % | Peak RSS MiB | Mean WER/CER |",
              "|---|---|---|---|---|---:|---:|---:|---:|---:|"]
    for s in summarize(runs):
        lines.append(f"| {s['language']} | {s['completed_runs']}/{s['failed_runs']} | {_range(s['time_to_first_transcript_seconds'])} | "
                     f"{_range(s['finalization_from_end_seconds'])} | {_range(s['total_end_to_end_seconds'])} | {_number(s['inference_rtf']['mean'])} | "
                     f"{_number(s['system_cpu_run_means_percent']['mean'])} | {_number(s['process_cpu_run_means_percent']['mean'])} | "
                     f"{_number(s['peak_process_rss_mib'])} | {s['error_metric']} {_number(s['mean_error_rate']['mean'], True)} |")
    lines += ["", "## Observations", ""]
    for s in summarize(runs):
        if not s["completed_runs"]:
            continue
        rtf = s["inference_rtf"]["mean"]
        interpretation = "faster than" if rtf < 1 else "slower than" if rtf > 1 else "approximately equal to"
        lines.append(f"- {s['language']}: mean inference RTF {_number(rtf)} is {interpretation} audio duration. "
                     f"First nonempty text arrived before the last chunk in {s['progressive_runs']}/{s['completed_runs']} runs.")
    if valid:
        slowest = max(summarize(runs), key=lambda s: s["inference_seconds"]["mean"] or -1)
        lines += [f"- {slowest['language']} had the highest mean total inference time among these recordings. "
                  "Recording durations/content differ, so this does not establish a general language-speed ranking.",
                  "- CPU monitoring shows utilization, but this experiment alone does not establish whether CPU, memory pressure, "
                  "or another resource caused a bottleneck. Background applications also contribute to system CPU.",
                  "- RSS snapshots below show observed variation, not a memory-leak diagnosis. Windows residency can change with paging and working-set trimming."]
    lines += ["", "### Backend memory before and after each stream", "",
              "| Language/run | Before MiB | After MiB | Minimum RSS MiB | Mean RSS MiB | Peak RSS MiB | Minimum available system MiB |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for row in runs:
        resource = row.get("resources")
        if not resource or not resource["samples"]:
            continue
        samples, stats = resource["samples"], resource["summary"]["process_rss_mib"]
        before = next((s["process_rss_bytes"] / MIB for s in samples if s["stage"] == "before"), None)
        after = next((s["process_rss_bytes"] / MIB for s in samples if s["stage"] == "after"), None)
        available = resource["summary"]["available_ram_mib"]["min"]
        lines.append(f"| {row['language']}/{row['run']} | " + " | ".join(_number(v) for v in [before, after, stats["min"], stats["mean"], stats["max"], available]) + " |")
    lines += ["", "## Accuracy evidence", "", "Raw references remain unchanged. See benchmark_methodology.md for normalization. Error rates may exceed 100%; they are not converted into 100% minus error rate.", ""]
    for row in valid:
        accuracy = row["accuracy"]
        lines += [f"### {row['language']} run {row['run']}", "", f"Reference: {accuracy['reference'] or 'NOT MEASURED'}", "",
                  f"Generated: {row['transcript']}", "",
                  f"{accuracy['metric']}: {_number(accuracy['error_rate'], True)}; substitutions={accuracy.get('substitutions')}, "
                  f"deletions={accuracy.get('deletions')}, insertions={accuracy.get('insertions')}, reference units={accuracy.get('reference_units')}.", ""]
    errors = result.get("setup_errors", []) + [f"{r['language']} run {r['run']}: {'; '.join(r['errors'])}"
                                              for r in runs + result.get("warmups", []) if r["errors"]]
    lines += ["## Failures", "", *(errors or ["No recorded setup/session failures."]), "", "## Limitations", "",
              "- Single Windows laptop, CPU only; thermal state, power mode, paging, other applications, and first-inference/cache effects can change results.",
              "- Three short recordings; repeated runs do not establish population-level language accuracy or performance.",
              "- Sequential non-overlapping windows are progressive inference, not native Qwen streaming. Boundary cuts and tiny residual windows can harm recognition.",
              "- One stream at a time. No capacity, concurrent-call, infrastructure, quantization, or deployment conclusions.",
              "- Resource sampling includes connection/validation setup around the stream, excludes child-process RSS/CPU, and may miss sub-interval peaks. RSS includes the whole backend, not model weights alone.",
              "- Inference RTF excludes queue/network/transmission time. Client durations never subtract backend timestamps.",
              "- Optional warm-ups and historical model loading are reported separately. Measurements missing from an older running backend remain unavailable.", ""]
    return "\n".join(lines)


def save_results(result: dict, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    # Strict JSON prevents NaN/Infinity from becoming misleading numeric measurements.
    serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary = directory / "benchmark_results.json.tmp"
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(directory / "benchmark_results.json")
    (directory / "benchmark_results.csv").write_text(_csv([csv_row(r) for r in result["runs"]]), encoding="utf-8-sig")
    resources = [{"language": r["language"], "run": r["run"], **s} for r in result["runs"] for s in r.get("resources", {}).get("samples", [])]
    (directory / "resource_samples.csv").write_text(_csv(resources), encoding="utf-8-sig")
    windows = [{"language": r["language"], "run": r["run"], **w} for r in result["runs"] for w in r.get("windows", [])]
    (directory / "window_metrics.csv").write_text(_csv(windows), encoding="utf-8-sig")
    (directory / "benchmark_summary.md").write_text(render_report(result), encoding="utf-8")
