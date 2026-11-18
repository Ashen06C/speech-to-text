# Measured concurrency results

Recorded: 2026-11-19T01:44:11.995426+05:30

Audio: `samples\audios\english.wav` (English, 12.153 seconds).

Hardware: 13th Gen Intel(R) Core(TM) i5-13420H; 8 physical / 12 logical processors; 15.669 GiB usable RAM.
OS: Windows-11-10.0.26200-SP0; Python 3.12.10; model Qwen3-ASR-0.6B on CPU.

Backend admission limit: 2; pending audio windows per session: 4; inference operations at once: 1; window: 3000 ms.
Configured torch threads: 6 (Live /api/system).

One call leg is one WebSocket session. All sessions share one serialized model. A connected socket is not evidence of sustainable real-time processing.

## Criteria

`{'cpu_percent': 90, 'ram_percent': 85, 'sustained_seconds': 3, 'min_available_mib': 1024, 'finalization_seconds': 5, 'queue_wait_seconds': 6, 'max_queue_depth': 4, 'inference_demand_limit': 1}`

Targets are configurable choices, not an assignment SLA. Acceptance requires every call to complete in every requested repetition, valid separate stream IDs/events, bounded observed waiting, an adequate serialized inference-time budget, and CPU/RAM within guardrails.

## Level summaries

Latency percentiles pool completed calls across repetitions. Failed/cancelled calls are counted separately and are not assigned fabricated latency values.

| Level | Runs | Complete / requested | Success | First text mean / P50 / P95 / max s | Finalization mean / P50 / P95 / max s | Meets criteria |
|---|---|---|---|---|---|---|
| 2 | 1/1 | 2/2 | 100.000% | 4.990 / 4.990 / 5.589 / 5.655 | 2.953 / 2.953 / 3.188 / 3.214 | True |

## CPU, memory and queue observations

| Level | System CPU mean / peak % | Backend CPU mean / peak % | Peak RSS MiB | Queue wait mean / max s | Test duration s |
|---|---|---|---:|---|---:|
| 2 | 41.605 / 56.700 | 438.143 / 594.700 | 3682.707 | 0.981 / 2.690 | 15.397 |

System CPU includes all programs, including this client. Backend CPU uses 100% per logical processor and may exceed 100%. RSS covers the entire backend, excludes children, and is not model-weight-only memory. CPU averages are weighted by sampling intervals; peaks are observed samples.

## Per-call outcomes

| Level/run/call | Stream ID | Status | Bytes/chunks | First s | Finalization s | End-to-end s | Error |
|---|---|---|---|---:|---:|---:|---|
| 2/1/1 | 9b73ccf2-5b7c-4bfc-a970-72ea56b37af8 | COMPLETED | 388902/25 | 5.655 | 2.692 | 14.854 |  |
| 2/1/2 | 8415c545-def3-4e0d-83d7-f72db59119a0 | COMPLETED | 388902/25 | 4.324 | 3.214 | 15.377 |  |

## Memory before and after

| Level/run | Before RSS MiB | After RSS MiB | Minimum available system MiB |
|---|---:|---:|---:|
| 2/1 | 3635.488 | 3633.672 | 4733.547 |

## Capacity conclusion

- Highest level attempted: 2
- Highest fully successful repeated level: 2
- Highest meeting short-recording criteria: 2
- Maximum established: False
- Untested requested levels: []
- Stopping reason: Requested sequence finished.

Inference is serialized: competing windows wait for the same execution slot, even when overall system CPU is below 100%. Extra streams do not create extra model instances. Per-session queues/buffers add memory; model weights are shared.

Exact live queue depth is unavailable on this server. Raw client unfinished-window proxies and real backend per-window wait times are saved separately; proxies are not relabeled as exact queue measurements. The sum of inference seconds divided by one audio duration tests whether the serialized model can supply the offered audio-time workload; short recordings still cannot prove indefinite stability.

Small samples, fixed language/content, Windows paging, laptop power/thermal changes, and uncontrolled background tasks limit conclusions. Admission rejection identifies a software cap, not a hardware maximum. Cancelled synchronous model work can finish safely on the server after clients close. No server capacity equivalence or production sizing is proven by these measurements.
