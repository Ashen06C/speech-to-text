# Technical report

Real-Time Multilingual Speech-to-Text on CPU. Measurements are from the retained local runs; infrastructure figures are conditional estimates. This is a prototype, not a production capacity guarantee.

## 1. Executive summary

The application simulates one live call leg per WebSocket by sending a WAV recording progressively and returning recognized text while transmission continues. React/TypeScript provides file selection, language, streaming controls, transcripts and basic timings. FastAPI validates sessions/audio and reuses one cached Qwen3-ASR-0.6B model through PyTorch CPU.

Nine streams completed across English, Mandarin Chinese and Bahasa Indonesia. Mean inference RTF was below one for each sample; recognition errors remained. Later concurrency tests completed nine calls over three one-call and three two-call rounds. One call passed all chosen sustainability criteria; two calls were borderline. A conservative sizing scenario is provided with explicit assumptions, not measured 50–1,000-call workloads.

## 2. Hardware and software environment

| Item | Environment |
|---|---|
| CPU | Intel Core i5-13420H; 8 physical cores, 12 logical processors |
| Memory | 16 GB installed; 15.669 GiB usable reported by psutil |
| OS / Python | Windows 11 Pro build 26200 (owner specification; runtime reports Windows 11 build 26200); Python 3.12.10 |
| Model | Qwen/Qwen3-ASR-0.6B, cached; no GPU |
| Inference | PyTorch 2.15.0+cpu; qwen-asr 0.0.6; Transformers 4.57.6 |
| Backend | FastAPI 0.163.2; Uvicorn 0.54.0; websockets 17.4 |
| Audio/resources/evaluation | NumPy 2.5.5; soundfile 0.14.0; psutil 7.2.2; JiWER 4.0.0 |
| Frontend | React/react-dom 19.3.1; TypeScript 7.0.2; Vite 8.3.8; Tailwind CSS 4.3.5 |
| Tooling | Node 24.18.0; npm 11.16.0; Vitest 5.1.1 |
| ASR settings | float32, CPU, six threads, batch limit 1, max new tokens 256 |
| Streaming settings | 500 ms chunks, 3,000 ms windows, four pending windows/session, two admitted sessions, one executor thread/model inference at a time |

Versions were read from the installed environment during final verification. Requirements and the frontend lockfile describe dependency resolution; a clean installation was not repeated.

## 3. Implementation approach

Qwen3-ASR is mandated by the assignment; the 0.6B model was retained to keep the existing working CPU integration. Python gives direct access to the model package, PyTorch and NumPy. FastAPI provides typed REST routes and native WebSocket support. React hooks manage UI lifecycle and incremental text; TypeScript validates the message contract. WebSocket allows audio upload and transcript events concurrently.

A 500 ms chunk contains 8,000 PCM16 samples, small enough for progressive delivery without one message per sample. Three-second windows reduce repeated model-call overhead compared with transcribing every chunk. The trade-off is collection latency and words split at window boundaries. CPU-only inference is the assignment constraint; no GPU or alternative model was used in measured tests.

## 4. Streaming workflow and timing

The client validates little-endian 16 kHz mono signed PCM16, sends `start`, waits for `ready`, then sends paced binary PCM without the WAV header. The backend decodes non-overlapping windows to normalized NumPy float32 and returns ordered `segment_transcript` events. On `end`, it processes residual audio and sends final text, metrics and completion. Chinese segments join without added spaces.

Transport takes approximately the recording duration. Inference time measures model work after acquiring the lock. Queue waiting covers pending/executor/lock delay. Client first-text latency ends at receipt of the first nonempty recognized segment. Client finalization ends at final-text receipt, starting immediately before sending `end`. End-to-end runs from streaming start to `completed`. All duration differences use one process's monotonic clock. Server/UI first-segment timing can include an empty text segment; it is not identical to the benchmark's nonempty-text definition.

## 5. Multilingual validation

The manifest pairs actual recordings with owner-verified UTF-8 references:

| UI language / backend value | Audio under `samples/audios/` | Duration | Recorded benchmark runs |
|---|---|---:|---|
| English / `English` | english.wav | 12.153 s | 3 completed |
| Mandarin Chinese / `Chinese` | mandarin_chinese.wav | 21.589 s | 3 completed |
| Bahasa Indonesia / `Indonesian` | bahasa_indonesia.wav | 17.832 s | 3 completed |

All formats passed validation. Each of the nine benchmark runs had nonempty text before audio transmission finished. Reference correctness was confirmed by the owner; the tool cannot independently certify what was spoken. Standalone earlier multilingual observation files are absent; retained benchmark events, references and automated multilingual tests provide the available evidence.

During final verification, one additional real stream per language passed through the running Vite proxy and existing backend without restart. All streamed/ordered/completed successfully and all three reference comparisons found differences. The validator's overall exit code was **1**, correctly reflecting imperfect transcription, not a WebSocket failure. These checks are not pooled into the historical benchmark averages.

## 6. Performance and accuracy measurements

Three sequential measured runs per language, no warm-up, same model and samples. Full runs/transcripts/resources are in [raw JSON](../benchmark_results/benchmark_results.json); definitions are in [benchmark methodology](benchmark_methodology.md).

| Language | First text s | Finalization s | End-to-end s | Inference s | Inference RTF | Error rate |
|---|---:|---:|---:|---:|---:|---|
| English | 6.036 | 1.921 | 14.078 | 8.201 | 0.675 | WER 20.000% |
| Chinese | 4.288 | 1.003 | 22.604 | 9.152 | 0.424 | CER 21.154% |
| Indonesian | 4.423 | 1.439 | 19.281 | 9.759 | 0.547 | WER 18.519% |

| Language | Mean / peak system CPU % | Mean / peak backend CPU % | Peak backend RSS MiB |
|---|---|---|---:|
| English | 47.326 / 90.300 | 328.677 / 609.700 | 3096.453 |
| Chinese | 42.441 / 90.400 | 266.428 / 615.600 | 3098.398 |
| Indonesian | 51.582 / 97.300 | 315.921 / 617.700 | 3102.496 |

CPU counters are primed/discarded, then sampled around every 500 ms in a background psutil thread. CPU means within a run are interval-weighted; this table averages run means. Backend CPU can exceed 100%: about 300% means three logical CPUs' worth of CPU time. Overall system CPU includes other applications and the client. RSS covers the whole backend process, excluding children; it is not isolated model-weight memory. 1 MiB = 2^20 bytes, 1 GiB = 2^30 bytes.

RTF = sum of window inference seconds / audio seconds, excluding transmission and queue time. Below one is faster-than-audio-duration model processing, not instantaneous text or guaranteed sustainable concurrency. The first English run took 8.871 s to first text; its cause was not isolated. Available RAM fell to about 0.086 GiB in the historical single-stream observations, so those runs include uncontrolled memory pressure.

WER/CER = (substitutions + deletions + insertions) / reference units. For example, one substitution among four words gives WER 25%; one wrong character among four gives CER 25%. NFKC normalization, casefolding and documented punctuation/whitespace rules are applied to copies of the text, never to the reference files themselves. Chinese uses characters without punctuation/whitespace; English/Indonesian use words. Contraction apostrophes are removed. Scores can exceed 100%; `100% - WER` is not guaranteed accuracy. Lower error rates are better. Empty/missing/unverified references are not scored.

Model loading time was unavailable during the single-stream benchmarks. A later normal start recorded **35.938 s** for cached loading, separately from inference and without a download. This is a different experiment.

## 7. Concurrency testing

19 November CPU profile: English 12.153 s, unchanged serialized model and two-session admission cap; three rounds at each tested level, one level at a time. Every call had an independent stream ID. Details and original-source hashes are in [concurrency results](concurrency_results.md).

| Calls | Completed / attempted | First text mean s | Final mean / P95 s | End-to-end mean s | Strict criterion rounds passed |
|---|---|---:|---|---:|---|
| 1 | 3 / 3 | 4.504 | 1.666 / 1.783 | 13.830 | 3 / 3 |
| 2 | 6 / 6 | 5.165 | 3.152 / 3.587 | 15.309 | 2 / 3 |

| Calls | System CPU mean / peak % | Process CPU mean / peak % | Peak RSS MiB | Queue wait mean / max s |
|---|---|---|---:|---|
| 1 | 38.889 / 99.000 | 280.883 / 612.900 | 4257.672 | 0.209 / 1.067 |
| 2 | 44.991 / 74.400 | 459.549 / 611.700 | 3699.063 | 1.076 / 3.087 |

No completed call failed. One two-call round failed the sustainability rule: total serialized inference / one recording duration was **1.013136**, about 1.3% above the limit of one. The other rounds were 0.990131 and 0.912672. All met the five-second finalization and six-second maximum window queue-wait targets. These are chosen prototype targets, not an assignment-provided SLA.

The sequence stopped at that performance failure; a same-level third confirmation completed safely. Levels 3–4 were not attempted. One call is the conservative repeatedly passing basis; **hardware maximum is not established**. Lower RSS at two calls reflects differing Windows residency/initialization, not negative per-call memory overhead. Increasing queue wait is consistent with serialized model service being limiting, even when whole-machine CPU is below 100%. Short English recordings do not prove long-call stability. Earlier RAM cancellations are retained separately and not averaged into completed rounds.

## 8. Infrastructure capacity estimation

**ESTIMATED — CONDITIONAL, low confidence.** No 50–1,000-call experiment or production server calibration occurred. The scenario assumes one 12-vCPU, 16-GiB server with one model worker performs no slower than the measured laptop worker. Laptop logical CPU count does not prove cloud-vCPU equivalence.

Worst passing per-call inference RTF = 0.555341. A 25% serialized inference-time reserve permits 0.75 / 0.555341 = 1.351 calls before rounding, capped at the passing measured bound of one. Effective calls/server = one. Headroom is applied once. This is deliberately conservative, not a minimum server requirement or purchasing recommendation.

| Call Legs | Estimated vCPU | Estimated RAM GiB | Estimated Servers | Reason / Assumption |
|---|---:|---:|---:|---|
| 50 | 600 | 800 | 50 | 1 effective call per assumed 12-vCPU, 16-GiB server |
| 100 | 1200 | 1600 | 100 | Same conditional server/throughput assumption |
| 200 | 2400 | 3200 | 200 | Same conditional server/throughput assumption |
| 500 | 6000 | 8000 | 500 | Same conditional server/throughput assumption |
| 1,000 | 12000 | 16000 | 1000 | Same conditional server/throughput assumption |

Servers = ceil(target / effective calls per server); aggregate vCPU/RAM = server count × assumed server specification. Memory uses measured whole-backend peak plus assumed 2-GiB OS/services reserve (about 6.158 GiB together), leaving provisioned 16 GiB; 64 MiB per extra stream is an assumption, not isolated measurement. There is no second numerical RAM-headroom multiplier. Model memory is shared per worker; additional replicas multiply the backend baseline. Queue, working memory, long calls and server CPU performance require calibration. [Calculator output and full formulas](capacity_estimation.md).

## 9. Challenges and supported solutions

- CPU model work is synchronous: executor/threadpool execution keeps the WebSocket receiver responsive while one shared lock protects inference.
- Context is lost at window edges: sequential windows make ordering and measurement understandable, but split words and recognition errors remain. Complex overlap correction was not implemented.
- Session cancellation cannot kill an executing PyTorch call safely: pending work is discarded and cleanup waits for ownership of the active job to end.
- Language labels differ from model identifiers: explicit English/Chinese/Indonesian mapping, Unicode tests and no added Chinese spaces handle the protocol correctly.
- Windows RAM guardrails cancelled earlier attempts: those failed experiments were preserved; safe later measurements were obtained without weakening model concurrency.
- PowerShell blocked `npm.ps1` during final checks: `npm.cmd` worked without changing execution policy. Cached model lookup prevents unexpected downloads.

These are observations from code/tests/execution, not invented personal development experiences.

## 10. Limitations

CPU-only, short sample recordings, one laptop, uncontrolled thermal/power/background conditions, limited concurrent scale, imperfect transcripts and window-boundary context loss. No native model streaming, microphone capture, telephony integration, authentication, production deployment or distributed runtime is implemented. UI CPU percentage is unavailable; benchmark tools provide CPU sampling. A fresh install and manual browser/clipboard demonstration still need confirmation. The personal video is pending.

## 11. Future improvements

Evaluate CPU-compatible quantization/native runtimes without assuming a speedup; compare multilingual whisper.cpp and documented Qwen C++ support using identical samples. Add VAD and context-aware segmentation/overlap handling with accuracy regression tests. Test longer and more diverse recordings. Calibrate independent ASR workers and bounded dispatch across servers, and add operational monitoring. 

## 12. Conclusion

The prototype connects validated WAV input, paced WebSocket transport, isolated sessions and shared CPU ASR to a progressive React transcript. Retained measurements demonstrate multilingual processing and successful two-call completion, while recognition errors and serialized inference limit claims about continuous capacity.
