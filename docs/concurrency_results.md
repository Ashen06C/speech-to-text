# Concurrency measurements — 19 November 2026

The retry completed **three one-stream rounds and three two-stream rounds**, using the real Qwen3-ASR-0.6B CPU model. All **nine calls completed**, returned final transcripts, and had distinct stream IDs. This establishes successful simultaneous streaming at two calls for this recording, not a production capacity guarantee.

Evidence: [combined observations](../benchmark_results/concurrency_results.json), [per-call CSV](../benchmark_results/concurrency_results.csv), and [generated report](../benchmark_results/concurrency_summary.md).

## Test setup

- Intel Core i5-13420H: 8 physical cores, 12 logical processors; 15.669 GiB usable RAM; Windows 11 build 26200; Python 3.12.10.
- Existing cached Qwen3-ASR-0.6B, PyTorch CPU; six configured and live torch threads. One worker served every measured call, PID 16036.
- The previous backend had stopped. A single backend was started normally; cached model loading took 35.938 seconds, separately from inference. No weights were downloaded.
- English recording: `samples/audios/english.wav`, 12.1531875 seconds, 16 kHz mono PCM16. Transmission used paced 500 ms chunks and three-second windows.
- Existing admission limit: two sessions. Shared model inference remained serialized, with four pending audio windows per session. No inference or admission limit was increased.
- Concurrent connection attempts began within 0.992 ms of one another at most. Sending and receiving ran concurrently.

## Actual results

Values pool completed calls across three rounds at each level. Latency uses client monotonic timestamps. Finalization starts immediately before sending `end`.

| Concurrent calls | Completed / attempted | Mean first text s | Mean finalization s | P95 finalization s | Mean end-to-end s | Strict criteria |
|---|---|---:|---:|---:|---:|---|
| 1 | 3 / 3 | 4.504 | 1.666 | 1.783 | 13.830 | Passed all three rounds |
| 2 | 6 / 6 | 5.165 | 3.152 | 3.587 | 15.309 | Passed two of three rounds |
| 3 | Not attempted | — | — | — | — | Stopped after the two-stream performance failure |
| 4 | Not attempted | — | — | — | — | Stopped after the two-stream performance failure |

| Concurrent calls | System CPU mean / peak % | Backend CPU mean / peak % | Peak backend RSS MiB | Window queue wait mean / max s | Minimum available RAM MiB |
|---|---|---|---:|---|---:|
| 1 | 38.889 / 99.000 | 280.883 / 612.900 | 4257.672 | 0.209 / 1.067 | 3800.707 |
| 2 | 44.991 / 74.400 | 459.549 / 611.700 | 3699.063 | 1.076 / 3.087 | 4695.828 |

Backend CPU can exceed 100% because 100% represents approximately one logical CPU. System CPU includes other applications and the benchmark client. The brief 99% system peak did not persist for the configured three seconds. No CPU, RAM, timeout, queue-depth, or finalization guardrail was violated in these completed rounds.

## Why two calls are classified as borderline

The acceptance rules were unchanged: every call must complete; finalization must stay within five seconds; window queue wait must stay within six seconds; CPU/RAM must remain within operating limits; total serialized model work must not exceed one recording duration.

| Two-stream round | Total inference time / one audio duration | Outcome |
|---|---:|---|
| 1 | 0.990131 | Passed |
| 2 | 1.013136 | Exceeded the inference-time budget by about 1.3% |
| 3 | 0.912672 | Passed |

All two-stream calls completed and met the latency targets. The failed rule concerns continuous offered inference work, not a connection error. This small exceedance on a short recording does not prove that two long calls are impossible. It does mean two calls did not consistently meet the selected strict criterion, so **one call per worker is the conservative passing basis for sizing**.

The default sequence stopped after round 2 at level 2. A third same-level confirmation was then run with identical settings because every earlier call had completed safely and no resource guardrail had fired. No higher level was attempted. Source experiments are preserved in [the original sequence](../benchmark_results/concurrency_20261119/concurrency_results.json) and [the confirmation](../benchmark_results/concurrency_20261119_two_stream_confirmation/concurrency_results.json). The combined file records source hashes and original run indexes; no timings or transcripts were synthesized.

## Capacity and bottlenecks

- Highest attempted level: **2**.
- Highest fully completed repeated level: **2**.
- Highest meeting every strict criterion: **1**.
- Hardware maximum sustainable capacity: **not established**.

Queue waiting and finalization increased at two calls while total model work approached the audio-time budget. This is consistent with the single serialized inference slot becoming the limiting service stage; overall machine CPU need not reach 100%. These observations do not isolate all effects of scheduling, background work, or Windows paging.

The model is shared between sessions. The lower observed RSS peak at two calls does not show negative per-call memory overhead: initialization and Windows working-set residency differed between rounds. Do not multiply model RAM by call count within one process.

The recording is short and only English was used for this load profile. Longer calls, mixed languages, repeated experiments, and a production CPU calibration are still needed before guaranteeing continuous-call capacity. The software admission cap of two is separate from the hardware limit.

## Earlier attempts and verification

Earlier RAM-guard cancellations remain preserved: [18 November observations](../benchmark_results/concurrency_20261118_guarded/concurrency_results.json) and [the first 19 November retry](../benchmark_results/concurrency_20261119_retry2/concurrency_results.json). Those attempts are not pooled into the successful latency/resource statistics.

After the measurements, the full backend regression suite ran **113 tests: 112 passed and one opt-in real REST test was skipped**. Focused load/capacity tests also passed, including the added inference-headroom cases. Actual model inference here was exercised through WebSockets.

See [conditional sizing](capacity_estimation.md) and [commands/measurement definitions](concurrency_methodology.md).
