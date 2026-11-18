# Performance and accuracy results

Full transcripts and individual runs are in the [generated report](../benchmark_results/benchmark_summary.md) and [raw JSON](../benchmark_results/benchmark_results.json). See [how to repeat the benchmark](benchmark_methodology.md).

## Test setup

| Item | Configuration |
|---|---|
| CPU | Intel Core i5-13420H; 8 physical cores, 12 logical processors |
| System RAM | 15.669 GiB usable |
| OS / Python | Windows 11 build 26200 / Python 3.12.10 |
| Model / runtime | Qwen3-ASR-0.6B / PyTorch 2.15.0+cpu |
| CPU threads | Configured as 6, inferred from process environment/source defaults |
| Audio / streaming | 16 kHz mono PCM16 WAV; 500 ms chunks; three-second inference windows |
| Runs | Three per language, sequential; no warm-up |

The existing model was reused without restarting the backend, downloading weights, or changing inference settings. References were confirmed by the project owner. Recordings are mapped in `samples/multilingual_manifest.json`.

## Measured results

**All nine streams completed**, with nonempty transcript updates before audio transmission finished in every run. Values below are means across three runs per language.

| Language | Audio duration s | First text s | Finalization s | End-to-end s | Inference RTF | Error rate |
|---|---:|---:|---:|---:|---:|---:|
| English | 12.153 | 6.036 | 1.921 | 14.078 | 0.675 | WER 20.000% |
| Chinese | 21.589 | 4.288 | 1.003 | 22.604 | 0.424 | CER 21.154% |
| Indonesian | 17.832 | 4.423 | 1.439 | 19.281 | 0.547 | WER 18.519% |

Finalization starts immediately before sending `end`. End-to-end time includes paced audio transmission. Inference RTF uses only model processing time, excluding transmission and queue waiting.

| Language | Mean system CPU % | Peak system CPU % | Mean backend CPU % | Peak backend CPU % | Peak backend RSS MiB |
|---|---:|---:|---:|---:|---:|
| English | 47.326 | 90.300 | 328.677 | 609.700 | 3096.453 |
| Chinese | 42.441 | 90.400 | 266.428 | 615.600 | 3098.398 |
| Indonesian | 51.582 | 97.300 | 315.921 | 617.700 | 3102.496 |

Backend CPU uses 100% per logical CPU, so values above 100% are expected. RSS covers the whole backend process, excluding children. Peak values are the highest observed samples.

## Accuracy

Each language produced the same transcript across its three runs. Counts below apply to each individual run after the documented normalization.

| Language | Reference units | Substitutions | Deletions | Insertions | Error rate |
|---|---:|---:|---:|---:|---:|
| English | 30 words | 2 | 1 | 3 | 20.000% WER |
| Chinese | 52 characters | 9 | 0 | 2 | 21.154% CER |
| Indonesian | 27 words | 4 | 1 | 0 | 18.519% WER |

Recognition errors remain. Successful streaming means the measurement completed, not that every word was recognized correctly. Original references and generated text are preserved in the results.

## Observations and limitations

- Every inference RTF was below 1. Model processing was faster than audio duration, while the user still waits for window collection and transcription.
- English's first-text times were 8.871, 4.631, and 4.605 seconds. The slower first run is included; its cause was not isolated.
- Backend RSS peaked at 3,102.496 MiB. Available system RAM fell to about **0.086 GiB**, indicating memory pressure. Background applications, temperature, and power mode were not controlled.
- Independent three-second windows can cut words and lose context. Extra English text and repeated Chinese punctuation were observed.
- Historical model loading time and live torch thread count were **NOT MEASURED** in these Step 6 runs because that server predates those diagnostics. A later Step 7 backend start recorded 35.938 seconds of cached loading and six live threads; see [concurrency results](concurrency_results.md). Those later values are not retroactive Step 6 measurements.
- These results cover three short recordings on one CPU laptop. They do not establish general language rankings, long-term memory stability, or a proven CPU bottleneck.
- Step 6 measured single streams only. Subsequent [real-model concurrency tests](concurrency_results.md) and [conditional sizing estimates](capacity_estimation.md) are recorded separately. Browser interaction itself was not benchmarked.

## Checks executed

| Check | Result |
|---|---|
| Sample validation | All three recordings passed |
| Backend tests | 83 passed; 1 opt-in real REST test skipped |
| Frontend tests | 41 passed |
| TypeScript and production build | Passed |
| Real CPU WebSocket benchmark | 9 completed; 0 failed |

The measured artifacts contain 57 transcription windows and 337 resource snapshots. Automated tests use synthetic fixtures and mocked inference; actual model inference was exercised separately through the nine WebSocket sessions.
