# Measured concurrency results

Recorded: 2026-11-19T01:41:58.133751+05:30

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
| 1 | 3/3 | 3/3 | 100.000% | 4.504 / 4.529 / 4.538 / 4.539 | 1.666 / 1.615 / 1.783 / 1.801 | True |
| 2 | 3/3 | 6/6 | 100.000% | 5.165 / 5.100 / 6.024 / 6.051 | 3.152 / 3.152 / 3.587 / 3.649 | False |

## CPU, memory and queue observations

| Level | System CPU mean / peak % | Backend CPU mean / peak % | Peak RSS MiB | Queue wait mean / max s | Test duration s |
|---|---|---|---:|---|---:|
| 1 | 38.889 / 99.000 | 280.883 / 612.900 | 4257.672 | 0.209 / 1.067 | 41.562 |
| 2 | 44.991 / 74.400 | 459.549 / 611.700 | 3699.062 | 1.076 / 3.087 | 46.805 |

System CPU includes all programs, including this client. Backend CPU uses 100% per logical processor and may exceed 100%. RSS covers the entire backend, excludes children, and is not model-weight-only memory. CPU averages are weighted by sampling intervals; peaks are observed samples.

## Per-call outcomes

| Level/run/call | Stream ID | Status | Bytes/chunks | First s | Finalization s | End-to-end s | Error |
|---|---|---|---|---:|---:|---:|---|
| 1/1/1 | 6d3e1625-ce46-441b-8776-ac5efb4f7d28 | COMPLETED | 388902/25 | 4.539 | 1.801 | 13.972 |  |
| 1/2/1 | d5f1fec1-bf8a-4ec9-a97e-0bd5bbba6bfd | COMPLETED | 388902/25 | 4.529 | 1.615 | 13.776 |  |
| 1/3/1 | 43c24baa-2ea9-40c2-8570-67eb923bf8da | COMPLETED | 388902/25 | 4.443 | 1.582 | 13.743 |  |
| 2/1/1 | fa505dff-3ec5-407c-8a02-46a5b263947a | COMPLETED | 388902/25 | 4.474 | 2.865 | 15.016 |  |
| 2/1/2 | 2922d4b9-7ed0-4db2-bed2-c436a45cbe11 | COMPLETED | 388902/25 | 5.943 | 3.401 | 15.552 |  |
| 2/2/1 | 9981d9f0-734e-4ae7-a896-6185cfffb762 | COMPLETED | 388902/25 | 6.051 | 3.090 | 15.247 |  |
| 2/2/2 | 63499ebd-a3e9-4197-a320-1585d3b696a0 | COMPLETED | 388902/25 | 4.545 | 3.649 | 15.808 |  |
| 2/3/1 | 9b73ccf2-5b7c-4bfc-a970-72ea56b37af8 | COMPLETED | 388902/25 | 5.655 | 2.692 | 14.854 |  |
| 2/3/2 | 8415c545-def3-4e0d-83d7-f72db59119a0 | COMPLETED | 388902/25 | 4.324 | 3.214 | 15.377 |  |

## Memory before and after

| Level/run | Before RSS MiB | After RSS MiB | Minimum available system MiB |
|---|---:|---:|---:|
| 1/1 | 4251.727 | 3678.750 | 3800.707 |
| 1/2 | 3678.750 | 3633.777 | 3961.984 |
| 1/3 | 3633.777 | 3633.160 | 4693.965 |
| 2/1 | 3633.160 | 3633.504 | 4716.980 |
| 2/2 | 3633.504 | 3635.473 | 4695.828 |
| 2/3 | 3635.488 | 3633.672 | 4733.547 |

## Capacity conclusion

- Highest level attempted: 2
- Highest fully successful repeated level: 2
- Highest meeting short-recording criteria: 1
- Maximum established: False
- Untested requested levels: [3, 4]
- Stopping reason: One of three two-stream rounds exceeded the unchanged serialized inference-time budget. Remaining repetition was completed as a same-level diagnostic; no higher concurrency was attempted.

Inference is serialized: competing windows wait for the same execution slot, even when overall system CPU is below 100%. Extra streams do not create extra model instances. Per-session queues/buffers add memory; model weights are shared.

Exact live queue depth is unavailable on this server. Raw client unfinished-window proxies and real backend per-window wait times are saved separately; proxies are not relabeled as exact queue measurements. The sum of inference seconds divided by one audio duration tests whether the serialized model can supply the offered audio-time workload; short recordings still cannot prove indefinite stability.

Small samples, fixed language/content, Windows paging, laptop power/thermal changes, and uncontrolled background tasks limit conclusions. Admission rejection identifies a software cap, not a hardware maximum. Cancelled synchronous model work can finish safely on the server after clients close. No server capacity equivalence or production sizing is proven by these measurements.
