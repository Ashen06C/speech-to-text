# Concurrency testing

One **call leg** is one independent WebSocket audio session. The load runner starts several clients through a shared asyncio event, then each sends 500 ms PCM chunks at real-time pace and receives its own transcript events. Each call records its stream ID, sent bytes/chunks, text, timings, completion, and errors. Responses must match the connection's ready stream ID.

## Existing backend limits

- Qwen3-ASR-0.6B runs on CPU and is loaded once per process.
- One streaming executor thread and the shared REST/WebSocket model lock allow **one model inference at a time**.
- The current configuration admits **two sessions**, with **four pending audio windows per session** and three-second windows.
- These limits are preserved. A connected WebSocket does not imply enough inference throughput for a continuous call.

The report identifies whether configuration came from live diagnostics or the process environment/source defaults. The older running server does not expose an exact live queue depth. The runner saves a client-derived unfinished-window proxy separately from actual backend queue waiting times; it does not claim the proxy is an exact server queue measurement.

## Run in PowerShell

Reuse the ready backend. Otherwise start it in a separate terminal:

```powershell
Set-Location D:\speech-to-text
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

After closing unnecessary applications to free RAM, run the sequence:

```powershell
Set-Location D:\speech-to-text
.\.venv\Scripts\python.exe -m backend.benchmarks.load_test --levels 1 2 3 4 --runs 3 --language English --audio samples/audios/english.wav --pause-between-levels --output benchmark_results/concurrency_retry
```

The runner proceeds only while the previous level passes every repetition. The current two-session admission limit prevents three/four successful sessions; overload rejection is recorded if those levels are reached. The limit is not increased for this experiment.

Single-level commands are also supported:

```powershell
.\.venv\Scripts\python.exe -m backend.benchmarks.load_test --concurrency 1 --runs 3 --output benchmark_results/level1
.\.venv\Scripts\python.exe -m backend.benchmarks.load_test --concurrency 2 --runs 3 --output benchmark_results/level2
.\.venv\Scripts\python.exe -m backend.benchmarks.load_test --concurrency 4 --runs 3 --output benchmark_results/level4
```

Prefer the sequential mode to avoid skipping the stability checks at lower levels. If `--audio` is omitted, the selected language's recording comes from `samples/multilingual_manifest.json`. `--language Chinese` and `--language Indonesian` are supported. Higher levels such as 6/8 require an explicit `--max-clients 8`; they should only be tried after lower levels are stable and resources permit.

Useful options:

| Option | Default | Purpose |
|---|---:|---|
| `--cpu-threshold` | 90 | Overall system CPU guardrail, percent. |
| `--ram-threshold` | 85 | Overall system RAM guardrail, percent. |
| `--sustained-seconds` | 3 | CPU saturation/backlog persistence duration. |
| `--min-available-mib` | 1024 | Additional available-RAM floor. |
| `--finalization-target` | 5 | Maximum acceptable end-to-final latency, seconds. |
| `--max-queue-wait` | 6 | Maximum per-window queue wait, seconds. |
| `--max-queue-depth` | 4 | Bound for the pending-window proxy. Does not change the server queue. |
| `--inference-demand-limit` | 1 | Maximum total serialized inference seconds / one recording duration. |
| `--timeout` | 120 | Per-call deadline including connection and streaming, seconds. |
| `--monitor-interval` | 0.5 | Resource sampling interval, seconds. |
| `--max-clients` | 4 | Maximum clients the runner may launch. |
| `--ws-url` | `ws://127.0.0.1:8000/ws/transcribe` | Existing streaming route. |

The five-second finalization and six-second queue targets are chosen demonstration limits, not an assignment SLA. They allow some waiting around the three-second windows while rejecting long backlogs. They are configurable. CPU/RAM thresholds are safety/operating choices, not definitions of real-time recognition quality. Do not raise them simply to obtain passing results on a memory-constrained machine.

## Measurements and acceptance

The runner reuses Step 6's client clock, event validation, backend PID identification, and psutil monitor. CPU counters are primed; one background monitor covers the whole round, rather than one monitor per client.

- First text: client stream start to the first nonempty recognized event.
- Finalization: immediately before sending `end` to final transcript receipt.
- End-to-end: client stream start to `completed` receipt.
- Window inference and queue wait: existing backend measurements, kept separate from client clocks.
- P50/P95: linear interpolation over sorted completed-call measurements; small sample counts limit percentile interpretation.
- CPU: interval-weighted mean and observed peak. Backend process CPU may exceed 100% (100% is one logical CPU). System CPU includes the client and all other applications.
- RSS: whole backend process, excluding child processes. Before/during/after snapshots and total/available system RAM are retained. MiB = 1024² bytes.

A level passes only if all requested repetitions and calls complete with correct events/IDs, deadlines and latency/queue targets are met, resource observations are reliable, no guardrail/persistent backlog violation occurs, and inference demand fits the configured audio-time budget. Summing all calls' inference times matters because they share a serialized model. A finite recording can drain after transmission even when continuous offered work would accumulate.

The report distinguishes the highest level attempted, highest fully successful repeated level, and highest meeting these short-recording criteria. Short samples and a software admission cap do not establish a hardware or production maximum. All active clients close and timers/tasks are cleaned up before another round. A synchronous model operation may finish safely on the backend after cancellation; failed rounds stop further testing rather than overlap a new workload with cleanup.

## Files and results

Tools are in `backend/benchmarks/`: `load_test.py`, `load_test_metrics.py`, `load_test_report.py`, and `capacity_estimator.py`. The Python client gained optional start synchronization and counters; the application/model configuration was not changed.

Actual attempted workloads produce `concurrency_results.json`, per-call `concurrency_results.csv`, and `concurrency_summary.md` in the selected output directory. JSON retains raw received events, resource samples, and backlog proxies. A preflight refusal before any clients start does not create load-result files. Failed/cancelled streams are counted and unavailable metrics remain null, rather than becoming zero latency. Use a new output directory to preserve earlier runs.

Run unit/regression tests without model inference:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -t . -v
```

## Capacity scenarios

Generate the current estimate/template from actual observations:

```powershell
.\.venv\Scripts\python.exe -m backend.benchmarks.capacity_estimator --input benchmark_results/concurrency_results.json --server-vcpu 12 --server-ram-gib 16 --server-capacity-factor 1 --headroom 0.25 --headroom-basis inference_time --output benchmark_results --report docs/capacity_estimation.md
```

Numerical scenarios require a repeatedly passing measured level, a passing one-stream RSS baseline, and an explicit `--server-capacity-factor` assumption or a separately calibrated production server. Defaults describe a hypothetical 12-vCPU/16-GiB server, not a proven match to the laptop. The factor is not inferred automatically from vCPU count.

The command above is an **uncalibrated scenario**, not proof that 12 cloud vCPU equal this laptop. It assumes one production worker runs the same workload no slower than the measured worker. The server specification and capacity factor are explicit assumptions requiring validation.

The calculator supports two headroom bases. The default `call_slots` mode reserves a fraction of nominal call slots; at one measured call, 25% headroom rounds down to zero whole calls. The selected `inference_time` mode instead reserves 25% of serialized model execution time, uses the worst passing per-call RTF, and caps the outcome at the measured passing call bound under the explicit server hypothesis. For the current measurements, RTF 0.555341 fits within the 0.75 time budget, so one call remains possible without inventing a fractional call or claiming extra measured capacity.

Both modes apply headroom once, round down to whole effective calls, check shared backend/working memory plus assumed stream overhead and OS reserve, then use `ceil(target / effective calls)` for server count. Memory multiplies with model replicas, not with every call in a shared process. A result below one whole call/server produces a template. The source result path and SHA-256 are saved with estimates. See [capacity estimation](capacity_estimation.md) for the conditional table and [actual results](concurrency_results.md) for the completed and earlier guarded experiments.
