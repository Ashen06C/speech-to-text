# Measured concurrency results

Recorded: 2026-11-18T19:44:50.021512+05:30

Audio: `samples\audios\english.wav` (English, 12.153 seconds).

Hardware: 13th Gen Intel(R) Core(TM) i5-13420H; 8 physical / 12 logical processors; 15.669 GiB usable RAM.
OS: Windows-11-10.0.26200-SP0; Python 3.12.10; model Qwen3-ASR-0.6B on CPU.

Backend admission limit: 2; pending audio windows per session: 4; inference operations at once: 1; window: 3000 ms.
Configured torch threads: 6 (Backend process environment or source defaults; live torch count unavailable on this already-running server).

One call leg is one WebSocket session. All sessions share one serialized model. A connected socket is not evidence of sustainable real-time processing.

## Criteria

`{'cpu_percent': 90, 'ram_percent': 85, 'sustained_seconds': 3, 'min_available_mib': 1024, 'finalization_seconds': 5, 'queue_wait_seconds': 6, 'max_queue_depth': 4, 'inference_demand_limit': 1}`

Targets are configurable choices, not an assignment SLA. Acceptance requires every call to complete in every requested repetition, valid separate stream IDs/events, bounded observed waiting, an adequate serialized inference-time budget, and CPU/RAM within guardrails.

## Level summaries

Latency percentiles pool completed calls across repetitions. Failed/cancelled calls are counted separately and are not assigned fabricated latency values.

| Level | Runs | Complete / requested | Success | First text mean / P50 / P95 / max s | Finalization mean / P50 / P95 / max s | Meets criteria |
|---|---|---|---|---|---|---|
| 1 | 1/3 | 0/1 | 0.000% | Unavailable / Unavailable / Unavailable / Unavailable | Unavailable / Unavailable / Unavailable / Unavailable | False |

## CPU, memory and queue observations

| Level | System CPU mean / peak % | Backend CPU mean / peak % | Peak RSS MiB | Queue wait mean / max s | Test duration s |
|---|---|---|---:|---|---:|
| 1 | 27.560 / 72.800 | 167.409 / 605.600 | 3140.875 | Unavailable / Unavailable | 7.047 |

System CPU includes all programs, including this client. Backend CPU uses 100% per logical processor and may exceed 100%. RSS covers the entire backend, excludes children, and is not model-weight-only memory. CPU averages are weighted by sampling intervals; peaks are observed samples.

## Per-call outcomes

| Level/run/call | Stream ID | Status | Bytes/chunks | First s | Finalization s | End-to-end s | Error |
|---|---|---|---|---:|---:|---:|---|
| 1/1/1 | 2a0cf035-9078-43a3-a41e-efd33e8f8d3c | CANCELLED | 128000/8 | Unavailable | Unavailable | Unavailable | Cancelled by operator or resource guard; not a completed stream. |

## Memory before and after

| Level/run | Before RSS MiB | After RSS MiB | Minimum available system MiB |
|---|---:|---:|---:|
| 1/1 | 2747.633 | 3108.656 | 2112.328 |

## Capacity conclusion

- Highest level attempted: 1
- Highest fully successful repeated level: None
- Highest meeting short-recording criteria: None
- Maximum established: False
- Untested requested levels: [2, 3, 4]
- Stopping reason: System RAM 86.83% reached 85.00% guardrail.; System RAM 86.30% reached 85.00% guardrail.; System RAM 86.11% reached 85.00% guardrail.; System RAM 86.03% reached 85.00% guardrail.; System RAM 86.08% reached 85.00% guardrail.; System RAM 86.10% reached 85.00% guardrail.; System RAM 86.09% reached 85.00% guardrail.; Not every requested call completed with valid transcript events.

Inference is serialized: competing windows wait for the same execution slot, even when overall system CPU is below 100%. Extra streams do not create extra model instances. Per-session queues/buffers add memory; model weights are shared.

Exact live queue depth is unavailable on this server. Raw client unfinished-window proxies and real backend per-window wait times are saved separately; proxies are not relabeled as exact queue measurements. The sum of inference seconds divided by one audio duration tests whether the serialized model can supply the offered audio-time workload; short recordings still cannot prove indefinite stability.

Small samples, fixed language/content, Windows paging, laptop power/thermal changes, and uncontrolled background tasks limit conclusions. Admission rejection identifies a software cap, not a hardware maximum. Cancelled synchronous model work can finish safely on the server after clients close. No server capacity equivalence or production sizing is proven by these measurements.
