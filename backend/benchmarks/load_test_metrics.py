"""Transparent concurrency acceptance rules and measured latency summaries."""

from dataclasses import asdict, dataclass
import math

from backend.benchmarks.benchmark_metrics import describe


@dataclass(frozen=True)
class Limits:
    cpu_percent: float = 90
    ram_percent: float = 85
    sustained_seconds: float = 3
    min_available_mib: float = 1024
    finalization_seconds: float = 5
    queue_wait_seconds: float = 6
    max_queue_depth: int = 4
    inference_demand_limit: float = 1

    def __post_init__(self):
        values = asdict(self)
        if any(not math.isfinite(v) or v <= 0 for v in values.values()):
            raise ValueError("All guardrail/acceptance values must be finite and positive.")
        if self.cpu_percent > 100 or self.ram_percent > 100:
            raise ValueError("System CPU/RAM percentages cannot exceed 100.")


def percentile(values: list[float], percent: float) -> float | None:
    """Linear interpolation of sorted observations, including small samples."""
    if not 0 <= percent <= 100:
        raise ValueError("Percentile must be between 0 and 100.")
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def latency_summary(values: list[float]) -> dict:
    return {**describe(values), "p50": percentile(values, 50), "p95": percentile(values, 95)}


def memory_violation(sample: dict, limits: Limits) -> str | None:
    total, available = sample["total_ram_bytes"], sample["available_ram_bytes"]
    used = 100 * (1 - available / total)
    if used >= limits.ram_percent:
        return f"System RAM {used:.2f}% reached {limits.ram_percent:.2f}% guardrail."
    if available / 1024**2 < limits.min_available_mib:
        return f"Available RAM {available / 1024**2:.1f} MiB is below {limits.min_available_mib:.1f} MiB."
    return None


def assess_round(round_result: dict, limits: Limits) -> dict:
    calls = round_result["calls"]
    successful = [c for c in calls if c["status"] == "COMPLETED"]
    reasons = list(round_result.get("guardrail_reasons", []))
    if len(successful) != round_result["concurrency"]:
        reasons.append("Not every requested call completed with valid transcript events.")
    stream_ids = [c.get("stream_id") for c in successful]
    if len(set(stream_ids)) != len(stream_ids) or any(not x for x in stream_ids):
        reasons.append("Completed call stream IDs are missing or duplicated.")
    if any(c["client_latency"]["finalization_from_end_seconds"] > limits.finalization_seconds for c in successful):
        reasons.append("Finalization latency exceeded the configured target.")
    waits = [w["queue_wait_time_seconds"] for c in successful for w in c["windows"]]
    if any(wait > limits.queue_wait_seconds for wait in waits):
        reasons.append("A window queue wait exceeded the configured target.")
    resources = round_result.get("resources", {})
    if resources.get("errors") or resources.get("summary", {}).get("cpu_sample_count", 0) < 1:
        reasons.append("Reliable resource samples were unavailable.")
    if round_result.get("queue_growth_detected"):
        reasons.append("Observed per-call backlog persisted or exceeded the configured bound.")
    demand = round_result.get("inference_demand_ratio")
    if demand is not None and demand > limits.inference_demand_limit:
        reasons.append("Total serialized inference work exceeded the configured audio-time budget.")
    cpu_duration = 0.0
    for sample in resources.get("samples", []):
        if violation := memory_violation(sample, limits):
            reasons.append(violation)
        cpu_duration = cpu_duration + sample.get("interval_seconds", 0) if (sample.get("system_cpu_percent") or 0) >= limits.cpu_percent else 0
        if cpu_duration >= limits.sustained_seconds:
            reasons.append("Sustained system CPU threshold exceeded.")
    # Failed overload sessions and bounded server queues cannot be accepted as capacity.
    return {"meets_criteria": not reasons, "reasons": list(dict.fromkeys(reasons)),
            "scope": "Short-recording operating criteria; not proof of long-call or production sustainability."}


def aggregate_level(rounds: list[dict], requested_runs: int) -> dict:
    concurrency = rounds[0]["concurrency"]
    calls = [c for r in rounds for c in r["calls"]]
    completed = [c for c in calls if c["status"] == "COMPLETED"]
    windows = [w for c in completed for w in c["windows"]]
    summary = {"concurrency": concurrency, "executed_runs": len(rounds), "required_runs": requested_runs,
               "requested_streams": len(calls), "completed_streams": len(completed),
               "failed_streams": len(calls) - len(completed),
               "success_rate": len(completed) / len(calls) if calls else None,
               "all_runs_successful": len(rounds) == requested_runs and len(completed) == len(calls),
               "meets_criteria": len(rounds) == requested_runs and all(r["acceptance"]["meets_criteria"] for r in rounds)}
    for metric in ("time_to_first_transcript_seconds", "finalization_from_end_seconds", "total_end_to_end_seconds"):
        summary[metric] = latency_summary([c["client_latency"][metric] for c in completed if c["client_latency"][metric] is not None])
    summary["window_queue_wait_seconds"] = latency_summary([w["queue_wait_time_seconds"] for w in windows])
    # Pool interval-weighted CPU means, not a misleading sum across simultaneous calls.
    from backend.benchmarks.benchmark_metrics import aggregate_resources
    summary["resources"] = aggregate_resources([s for r in rounds for s in r["resources"]["samples"]])
    summary["total_test_seconds"] = sum(r["elapsed_seconds"] for r in rounds)
    return summary


def select_capacity(levels: list[dict]) -> dict:
    attempted = [s["concurrency"] for s in levels]
    successful = [s["concurrency"] for s in levels if s["all_runs_successful"]]
    accepted = [s["concurrency"] for s in levels if s["meets_criteria"]]
    highest = max(accepted, default=None)
    return {"highest_attempted": max(attempted, default=None),
            "highest_successful": max(successful, default=None), "highest_meeting_criteria": highest,
            "next_higher_tested": highest is not None and highest + 1 in attempted,
            "maximum_established": False,
            "note": "Highest passing short-sample level only. Admission limits, missing levels, and short runs do not establish hardware or production maximum."}
