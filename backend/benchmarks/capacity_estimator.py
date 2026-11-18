"""Conditional planning scenarios from actual Step 7 measurements, not guarantees."""

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import sys

from backend.benchmarks.load_test_metrics import select_capacity
from backend.benchmarks.load_test_report import csv_text
from backend.validate_samples import ROOT, file_hash

TARGETS = (50, 100, 200, 500, 1000)


@dataclass(frozen=True)
class ServerAssumptions:
    vcpu: int = 12
    ram_gib: float = 16
    capacity_factor: float | None = None
    replicas: int = 1
    headroom: float = .25
    extra_stream_mib: float = 64
    os_reserve_gib: float = 2
    headroom_basis: str = "call_slots"

    def __post_init__(self):
        if self.vcpu < 1 or self.replicas < 1:
            raise ValueError("Server vCPU and replicas must be positive integers.")
        values = [self.ram_gib, self.extra_stream_mib, self.os_reserve_gib, self.headroom]
        if not all(math.isfinite(x) for x in values) or self.ram_gib <= 0 or self.extra_stream_mib <= 0 or self.os_reserve_gib < 0 or not 0 <= self.headroom < 1:
            raise ValueError("Invalid RAM, overhead, or headroom assumptions.")
        if self.capacity_factor is not None and (not math.isfinite(self.capacity_factor) or self.capacity_factor <= 0):
            raise ValueError("Explicit capacity factor must be finite and positive.")
        if self.headroom_basis not in {"call_slots", "inference_time"}:
            raise ValueError("Headroom basis must be call_slots or inference_time.")


def estimate(data: dict | None, assumptions: ServerAssumptions) -> dict:
    warnings = ["ESTIMATED, low-confidence conditional scenario; no production server was benchmarked.",
                "12 laptop logical processors are not proof of equivalence to 12 cloud vCPU.",
                "CPU, language mix, continuous-call duration, queues, power, and thermal behavior may scale nonlinearly.",
                "Multiple replicas require separate model/runtime memory and CPU calibration; memory is not multiplied per call within one replica."]
    output = {"assumptions": asdict(assumptions), "warnings": warnings, "status": "NOT ESTIMATED",
              "measured_streams": None, "effective_calls_per_server": None, "rows": []}
    reason = None
    if (not isinstance(data, dict) or not isinstance(data.get("rounds"), list) or not data["rounds"] or
            not isinstance(data.get("levels"), list) or not data["levels"]):
        reason = "No measured concurrency workload is available."
    elif any(not isinstance(level, dict) or not all(k in level for k in ("concurrency", "meets_criteria", "all_runs_successful"))
             for level in data["levels"]):
        reason = "Measured concurrency summaries are incomplete."
    else:
        capacity = select_capacity(data["levels"])
        output["measured_capacity"] = capacity
        output["measured_streams"] = capacity["highest_meeting_criteria"]
        if not output["measured_streams"]:
            reason = "No repeatedly tested concurrency level met the operating criteria; this does not prove zero physical capacity."
        elif assumptions.capacity_factor is None:
            reason = "Specify an explicit --server-capacity-factor hypothesis or obtain a production-server calibration. No laptop/vCPU equivalence is assumed."
    if reason is None:
        accepted = output["measured_streams"]
        baseline_peak = next((s.get("resources", {}).get("process_rss_mib", {}).get("max")
                              for s in data["levels"] if s["concurrency"] == 1 and s["meets_criteria"]), None)
        admission = data.get("backend_configuration", {}).get("max_sessions")
        if baseline_peak is None or not admission:
            reason = "A passing one-stream process-memory baseline and known backend admission limit are required."
        else:
            # CPU scaling is an explicitly unverified hypothesis, not automatic
            # proportional scaling from the assumed vCPU specification.
            assumed_calls = min(accepted * assumptions.capacity_factor * assumptions.replicas,
                                admission * assumptions.replicas)
            after_cpu_headroom = math.floor(assumed_calls * (1 - assumptions.headroom))
            if assumptions.headroom_basis == "inference_time":
                # Reserve serialized inference time rather than a fraction of an
                # indivisible call. Still never exceed the measured call bound
                # under the explicitly assumed server speed/replica factor.
                work_per_call = []
                for run in data["rounds"]:
                    if not isinstance(run, dict) or run.get("concurrency") != accepted or not run.get("acceptance", {}).get("meets_criteria"):
                        continue
                    calls = run.get("calls", [])
                    if len(calls) != accepted or any(c.get("status") != "COMPLETED" for c in calls):
                        continue
                    for call in calls:
                        seconds, duration = call.get("inference_seconds"), call.get("audio_duration_seconds")
                        if (isinstance(seconds, (int, float)) and isinstance(duration, (int, float)) and
                                math.isfinite(seconds) and math.isfinite(duration) and seconds > 0 and duration > 0):
                            work_per_call.append(seconds / duration)
                if not work_per_call:
                    reason = "No complete passing per-call inference/audio measurements for inference-time headroom."
                else:
                    worst_rtf = max(work_per_call)
                    inference_capacity = assumptions.capacity_factor * assumptions.replicas * (1 - assumptions.headroom) / worst_rtf
                    after_cpu_headroom = math.floor(min(assumed_calls, inference_capacity))
                    output.update(worst_measured_passing_call_rtf=worst_rtf,
                                  inference_time_capacity_after_headroom=inference_capacity,
                                  measured_call_bound=assumed_calls)
            fixed_gib = baseline_peak / 1024 * assumptions.replicas + assumptions.os_reserve_gib
            # Baseline RSS includes one call's runtime/working memory per replica.
            budget = assumptions.ram_gib - fixed_gib
            memory_calls = 0 if budget < 0 else assumptions.replicas + math.floor(budget * 1024 / assumptions.extra_stream_mib)
            effective = min(after_cpu_headroom, memory_calls)
            output.update(shared_backend_peak_mib_per_replica=baseline_peak,
                          fixed_process_and_os_gib=fixed_gib,
                          assumed_calls_before_headroom=assumed_calls,
                          calls_after_cpu_headroom=after_cpu_headroom, memory_limited_calls=memory_calls,
                          effective_calls_per_server=effective)
            if reason:
                pass
            elif effective < 1:
                reason = "Assumed server cannot support one whole call after the specified headroom/memory limits. Revise/calibrate the scenario; fractional calls are not allocated to servers."
            else:
                output["status"] = "ESTIMATED — CONDITIONAL"
                required_gib = fixed_gib + max(0, effective - assumptions.replicas) * assumptions.extra_stream_mib / 1024
                output["planned_memory_per_server_gib"] = required_gib
    for target in TARGETS:
        if reason:
            row = {"call_legs": target, "estimated_vcpu": None, "estimated_ram_gib": None, "estimated_servers": None,
                   "status": "NOT ESTIMATED", "reason": reason}
        else:
            servers = math.ceil(target / output["effective_calls_per_server"])
            row = {"call_legs": target, "estimated_vcpu": servers * assumptions.vcpu,
                   "estimated_ram_gib": servers * assumptions.ram_gib, "estimated_servers": servers,
                   "status": "ESTIMATED — CONDITIONAL",
                   "reason": f"{output['effective_calls_per_server']} effective whole calls/server; explicit capacity factor {assumptions.capacity_factor}; "
                             f"{assumptions.headroom:.0%} headroom applied once to {assumptions.headroom_basis}; {assumptions.replicas} model replicas/server. Uncalibrated server hypothesis."}
        output["rows"].append(row)
    return output


def render_estimates(result):
    lines = ["# Capacity estimation — ESTIMATED scenarios only", "", f"Status: **{result['status']}**", "",
             "No 50–1,000-call workload was run. Blank sizing values mean measurements or explicit server calibration are insufficient.", "",
             f"Highest measured short-recording level meeting criteria: {result['measured_streams']}", "",
             f"Source observations: `{result.get('source', {}).get('path', 'Provided measurement data')}`", "",
             f"Server assumptions: `{result['assumptions']}`", "",
             "| Call Legs | Estimated vCPU | Estimated RAM GiB | Estimated Servers | Reason / Assumption |",
             "|---|---:|---:|---:|---|"]
    for row in result["rows"]:
        show = lambda k: row[k] if row[k] is not None else "Not estimated"
        lines.append(f"| {row['call_legs']:,} | {show('estimated_vcpu')} | {show('estimated_ram_gib')} | {show('estimated_servers')} | {row['reason']} |")
    if "worst_measured_passing_call_rtf" in result:
        lines += ["", "## This scenario", "",
                  f"- Worst observed passing per-call inference RTF: {result['worst_measured_passing_call_rtf']:.6f}.",
                  f"- Calls allowed by the inference-time budget after headroom: {result['inference_time_capacity_after_headroom']:.3f} before rounding/bounds.",
                  f"- Measured call bound under the explicit server hypothesis: {result['measured_call_bound']:.3f}.",
                  f"- Effective whole calls per assumed server: {result['effective_calls_per_server']}.",
                  f"- Fixed backend/working-memory baseline plus OS reserve: {result['fixed_process_and_os_gib']:.3f} GiB.",
                  "", "The capacity factor of 1 means the assumed production worker must run this workload no slower than the measured worker. "
                  "It is a condition to validate, not evidence that laptop logical CPUs and cloud vCPU are equivalent. "
                  "These are conservative planning budgets, not proven minimum requirements or a hardware purchase recommendation."]
    lines += ["", "## Formulas", "",
              "1. Assumed calls/server = min(measured passing calls × explicit server-capacity factor × replicas, configured session limit × replicas).",
              "2. Apply headroom once. In call_slots mode: floor(assumed calls/server × (1 − headroom)). "
              "In inference_time mode: floor(min(assumed calls/server, capacity factor × replicas × (1 − headroom) / worst passing per-call RTF)).",
              "3. Fixed memory = passing one-call peak backend RSS × model replicas + assumed OS/services reserve. This measured RSS includes runtime and working memory, not just model weights.",
              "4. Extra calls consume assumed per-stream overhead (including buffers/queues). Memory budget limits effective whole calls/server; if one call cannot fit, no numerical scenario is issued.",
              "5. Servers = ceil(target / effective calls/server). Aggregate vCPU/RAM = servers × assumed server vCPU/RAM.",
              "", "Headroom is not applied a second time in server counting. Existing latency/operating criteria are selection rules, not an additional numerical CPU multiplier. "
              "Inference-time headroom reserves model execution wall time, not a percentage of physical CPU utilization. It does not claim more calls than the passing measured bound under the explicit server hypothesis. "
              "Default 64 MiB extra-stream overhead and 2 GiB OS reserve are planning assumptions, not measured isolated allocations. "
              "Per-worker model weights are shared between streams; replicas multiply model memory.",
              "", "## Limitations", "", *[f"- {w}" for w in result["warnings"]], ""]
    return "\n".join(lines)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--input", type=Path, default=ROOT / "benchmark_results" / "concurrency_results.json")
    cli.add_argument("--output", type=Path, default=ROOT / "benchmark_results")
    cli.add_argument("--report", type=Path, default=ROOT / "docs" / "capacity_estimation.md")
    cli.add_argument("--server-vcpu", type=int, default=12)
    cli.add_argument("--server-ram-gib", type=float, default=16)
    cli.add_argument("--server-capacity-factor", type=float)
    cli.add_argument("--replicas-per-server", type=int, default=1)
    cli.add_argument("--headroom", type=float, default=.25)
    cli.add_argument("--headroom-basis", choices=["call_slots", "inference_time"], default="call_slots")
    cli.add_argument("--extra-stream-mib", type=float, default=64)
    cli.add_argument("--os-reserve-gib", type=float, default=2)
    args = cli.parse_args()
    try:
        assumptions = ServerAssumptions(args.server_vcpu, args.server_ram_gib, args.server_capacity_factor,
                                        args.replicas_per_server, args.headroom, args.extra_stream_mib, args.os_reserve_gib,
                                        args.headroom_basis)
        data = json.loads(args.input.read_text(encoding="utf-8")) if args.input.is_file() else None
        results = estimate(data, assumptions)
        results["source"] = {"path": str(args.input.resolve()),
                             "sha256": file_hash(args.input) if args.input.is_file() else None,
                             "recorded_at": data.get("recorded_at") if isinstance(data, dict) else None}
    except (ValueError, OSError) as exc:
        cli.error(str(exc))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_estimates(results), encoding="utf-8")
    if data and data.get("rounds"):
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "capacity_estimates.csv").write_text(csv_text(results["rows"]), encoding="utf-8-sig")
        (args.output / "capacity_estimates.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(f"{results['status']}. Report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
