# How to run the benchmarks

The benchmark uses the existing Qwen3-ASR-0.6B CPU backend. It sends one WebSocket stream at a time and reuses the loaded model.

## Run in PowerShell

Use the existing `.venv`. JiWER 4.0.0 is the additional accuracy dependency in `backend/requirements.txt`.

If the backend is not already running, start it in a separate terminal:

```powershell
Set-Location D:\speech-to-text
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Validate the recordings and run three measured streams per language:

```powershell
Set-Location D:\speech-to-text
.\.venv\Scripts\python.exe -m backend.validate_samples --require-all
.\.venv\Scripts\python.exe -m backend.benchmarks.benchmark --all --runs 3 --output-dir benchmark_results/new_run
```

The manifest at `samples/multilingual_manifest.json` maps each recording to its language and reference. Audio must be WAV, 16 kHz, mono, signed PCM16. Close other transcription sessions before benchmarking. Use a new output directory to preserve earlier results.

To benchmark one recording with a reference you have checked:

```powershell
.\.venv\Scripts\python.exe -m backend.benchmarks.benchmark --language English --audio samples/audios/english.wav --reference samples/references/english_reference.txt --reference-verified --runs 3 --output-dir benchmark_results/english
```

Useful options: `--runs 1` for a shorter test, `--warmup` for one separate warm-up per language, `--monitor-interval 0.5` for sampling frequency, and `--url` for the WebSocket address. Warm-ups are excluded from measured summaries. Use `--help` for all options.

## What is measured

After `ready`, the client starts its timer and sends raw PCM in paced 500 ms chunks (8,000 samples / 16,000 bytes). It receives transcripts while sending. The backend processes independent three-second windows and the final shorter window.

| Metric | Meaning |
|---|---|
| First transcript | Client stream start to receipt of the first nonempty recognized text. |
| Finalization | Immediately before sending `end` to receiving the final transcript. Last-audio-to-final is also saved separately. |
| End-to-end | Client stream start to receipt of `completed`; includes audio transmission. |
| Window inference | Backend time executing the model, excluding loading and lock waiting. |
| Queue wait | Time from window enqueue to inference start, including executor/model-lock waiting. |
| Inference RTF | Sum of window inference times divided by audio duration. Below 1 means inference took less time than the recording duration. |
| Model loading | Separate cached startup-load duration, when available. |

Durations use `time.perf_counter()`. Client and backend timestamps are never subtracted from each other. The headline first-transcript metric ignores empty text events.

## CPU and RAM

A background thread uses psutil to sample the local backend every 500 ms by default. CPU counters are initialized and their first reading discarded.

- **System CPU:** utilization of the whole machine, on a 0–100% scale.
- **Backend CPU:** 100% represents about one logical CPU; 300% represents about three. This value can exceed 100%.
- **Backend RSS:** resident RAM of the whole backend process, including Python and model memory. Child processes are excluded.
- **System RAM:** total and available RAM are recorded separately from backend RSS.

RAM is sampled before, during, and after each stream. CPU means are weighted by sample duration; RSS means average the observed samples. Peaks are the highest observed samples and can miss shorter spikes. Monitoring includes a small amount of setup around the stream.

1 MiB = 1,048,576 bytes; 1 GiB = 1,073,741,824 bytes. An older running backend may not expose loading time or live torch thread count; these remain **NOT MEASURED**.

## Accuracy

JiWER calculates **WER** for English/Indonesian and **CER** for Chinese:

`Error rate = (substitutions + deletions + insertions) / reference words or characters`

Comparison copies use Unicode NFKC normalization and case folding. English/Indonesian remove apostrophes, replace other punctuation with spaces, and collapse whitespace. Chinese removes punctuation and whitespace. Original references and transcripts are preserved.

Missing, empty, or unverified references produce **NOT MEASURED**. An empty generated transcript against a valid reference is all deletions. Lower error rates are better; they can exceed 100% and are not converted into guaranteed accuracy using `100% - WER`.

## Output and tests

| File in the output directory | Contents |
|---|---|
| `benchmark_results.json` | Complete runs, transcripts, metrics, samples, and failures. |
| `benchmark_results.csv` | One row per measured run. |
| `resource_samples.csv` | CPU and RAM over time. |
| `window_metrics.csv` | Audio offsets, text, inference, and queue time per window. |
| `benchmark_summary.md` | Generated readable results report. |

Failed sessions retain available evidence and are excluded from numeric summaries. Further streams stop after a failure. Output files in the selected directory are overwritten.

Run checks from the project root:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -t . -v
Set-Location frontend
npm.cmd test
npm.cmd run build
```

See [recorded results](performance_benchmark.md), [psutil documentation](https://psutil.readthedocs.io/stable/index.html), and [JiWER documentation](https://jitsi.github.io/jiwer/reference/process/).
