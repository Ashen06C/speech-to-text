# Real-Time Multilingual Speech-to-Text on CPU

Assignment prototype that sends a selected WAV recording at approximately real-time speed and displays progressive transcription. React and FastAPI communicate through WebSocket; one cached Qwen3-ASR-0.6B model runs on CPU using PyTorch.

Supported UI languages: **English**, **Mandarin Chinese**, and **Bahasa Indonesia**. Backend identifiers are `English`, `Chinese`, and `Indonesian`. This is file-based call simulation, not a microphone or telephony service.

## How it works

The browser validates RIFF/WAVE structure, extracts raw PCM, and sends 500 ms chunks (16,000 bytes). FastAPI groups them into independent three-second windows and runs inference away from the event loop. Segments, final text and timings return on the same connection. This is **window-based progressive inference**, not native model-level streaming.

| Technology | Purpose and reason |
|---|---|
| React + TypeScript | Interactive transcript/state updates with typed protocol messages. |
| Vite + Tailwind CSS | Local development proxy, production build and responsive styling. |
| FastAPI | REST diagnostics, WAV transcription and native WebSocket handling. |
| WebSocket | Concurrent audio sending and transcript receiving over a persistent connection. |
| Python + PyTorch CPU | Direct integration with the installed Qwen package and NumPy audio. |
| Qwen3-ASR-0.6B | Assignment-required ASR family; smaller available model retained for this CPU prototype. |

## Prerequisites

- Windows 11; Python **3.12**. Verified environment: 3.12.10.
- Node.js and npm. Verified environment: Node 24.18.0, npm 11.16.0.
- RAM for Windows, the model and inference. This 16-GB laptop encountered RAM pressure with other applications open; close unused applications before benchmarking.
- Several GB of free storage for dependencies and model cache; an internet connection for the **initial** dependency/model download.
- No CUDA, GPU, vLLM or FlashAttention installation is required.

## Install on Windows PowerShell

Run from the project directory. Reuse an existing working `.venv`; do not recreate or reinstall it unnecessarily.

```powershell
Set-Location D:\speech-to-text
py -3.12 --version
# Only if .venv does not already exist:
py -3.12 -m venv .venv

# Optional activation, if your PowerShell execution policy permits it:
.\.venv\Scripts\Activate.ps1
```

All commands below use the environment executable directly, so activation is optional. On a **new** environment, install the CPU wheel first, then the repository requirements:

```powershell
.\.venv\Scripts\python.exe -m pip install torch==2.15.0+cpu --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

The backend deliberately resolves **cached weights only**. If the model is not cached on a new machine, perform this explicit initial download once:

```powershell
.\.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen3-ASR-0.6B')"
```

Do not run that download for this existing project. Fresh installation and initial download were **not repeated** during final verification; the installed environment and cached model were used. Pinned installed versions are in `backend/requirements.txt`; frontend resolution is in `frontend/package-lock.json`.

```powershell
Set-Location D:\speech-to-text\frontend
npm.cmd ci
if (!(Test-Path .env.local)) { Copy-Item .env.example .env.local }
```

`npm.cmd` avoids PowerShell blocking the `npm.ps1` wrapper. Dependency installation is for setup; final checks reused the installed packages.

The example frontend configuration is:

```dotenv
VITE_BACKEND_TARGET=http://127.0.0.1:8000
VITE_API_BASE_URL=/backend
VITE_WS_URL=/ws/transcribe
```

Vite forwards `/backend/*` HTTP requests and `/ws/*` WebSockets to FastAPI. Keep relative URLs for local use; setting a separate HTTP origin would require an intentional CORS setup. Restart Vite after changing environment variables. No secrets belong in `VITE_*` values because they are browser-visible.

## Run

Terminal 1, project root:

```powershell
Set-Location D:\speech-to-text
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Keep **one worker**, without `--reload`, to avoid model duplication during ordinary use/measurement. Wait for startup and model readiness. Backend settings come from process environment, not an automatically read Python `.env` file:

| Setting | Default |
|---|---:|
| `ASR_CPU_THREADS` | 6 |
| `ASR_WINDOW_MS` | 3000 |
| `ASR_PENDING_WINDOWS` | 4 per session |
| `ASR_MAX_STREAMS` | 2 admitted sessions |
| `ASR_MAX_STREAM_SECONDS` | 600 |
| `ASR_MAX_UPLOAD_MB` | 10 (REST file validation) |

These are configuration limits, not measured capacity guarantees. A shared lock serializes REST and WebSocket inference.

Terminal 2:

```powershell
Set-Location D:\speech-to-text\frontend
npm.cmd run dev
```

Open **http://127.0.0.1:5173**. Stop a terminal server with Ctrl+C. If a server is already running, reuse it rather than launching another model process.

Terminal 3:

```powershell
Set-Location D:\speech-to-text
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/system
```

Swagger: **http://127.0.0.1:8000/docs**. Expand `POST /api/transcribe`, choose **Try it out**, upload a valid WAV, select an exact backend language and execute. That REST route transcribes a complete file; the UI streaming workflow uses `/ws/transcribe` instead. Swagger does not provide this application's interactive WebSocket client.

## Use the application

1. Wait for backend connected/model ready indicators.
2. Select a **16 kHz, mono, signed PCM16 little-endian WAV**. Unsupported files are rejected without resampling.
3. Choose the matching language and press **Start Streaming**.
4. Watch audio transmission progress and ordered transcript segments arrive concurrently.
5. After sending finishes, wait for remaining inference and completion. Review/copy the transcript and metrics.
6. **Stop Streaming** cancels a session; it does not indicate successful completion. **Reset** clears the selected file and session state.

Samples and verified references are mapped in [samples/multilingual_manifest.json](samples/multilingual_manifest.json). The English benchmark recording is `samples/audios/english.wav`; the original `samples/english.wav` and `test_asr.py` are preserved separately.

The UI shows server latency, inference time and process memory. CPU percentage is explicitly **Not provided** in the dashboard; CPU utilization is recorded by benchmark tooling. Transmission progress is not inference progress.

## Tests and measurements

All Python commands below run from `D:\speech-to-text`.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -t . -v
.\.venv\Scripts\python.exe -m unittest backend.tests.test_multilingual -v
.\.venv\Scripts\python.exe -m backend.validate_samples --require-all
.\.venv\Scripts\python.exe -m backend.test_streaming_client --help
```

For a direct real WebSocket smoke test while the backend is ready:

```powershell
.\.venv\Scripts\python.exe -m backend.test_streaming_client --file samples/audios/english.wav --language English --chunk-duration-ms 500 --url ws://127.0.0.1:8000/ws/transcribe
```

Frontend checks:

```powershell
Set-Location D:\speech-to-text\frontend
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
```

With the model-ready backend running, actual multilingual validation (writes a new observation file):

```powershell
Set-Location D:\speech-to-text
.\.venv\Scripts\python.exe -m backend.validate_multilingual --output benchmark_results/manual_multilingual.json
```

The validator returns exit code 1 when recognized words differ from the verified reference, even if streaming succeeds. Inspect `streaming_result` separately from `reference_result`. This is not a claim of perfect transcription.

Repeat single-stream measurements in a **new output folder**:

```powershell
.\.venv\Scripts\python.exe -m backend.benchmarks.benchmark --all --runs 3 --output-dir benchmark_results/new_single_stream
```

Concurrency tooling is resource guarded and stops increasing levels after a failing round:

```powershell
.\.venv\Scripts\python.exe -m backend.benchmarks.load_test --concurrency 1 --runs 3 --output benchmark_results/new_baseline
# Optional sequential experiment; do not raise admission/inference limits to force a result:
.\.venv\Scripts\python.exe -m backend.benchmarks.load_test --levels 1 2 3 4 --runs 3 --pause-between-levels --output benchmark_results/new_concurrency
```

The retained measurements stopped at two calls; higher levels are not established. See [guardrails and acceptance rules](docs/concurrency_methodology.md) before running load tests.

Reproduce the **conditional** sizing scenario from retained observations without running inference:

```powershell
.\.venv\Scripts\python.exe -m backend.benchmarks.capacity_estimator --input benchmark_results/concurrency_results.json --server-vcpu 12 --server-ram-gib 16 --server-capacity-factor 1 --replicas-per-server 1 --headroom 0.25 --headroom-basis inference_time --output benchmark_results/recalculated_capacity --report benchmark_results/recalculated_capacity/capacity_estimation.md
```

`--server-capacity-factor 1` is an uncalibrated hypothesis that the production worker matches the measured worker. It is not proof that laptop logical processors equal cloud vCPU.

## Results and documentation

- [Final technical report](docs/final_technical_report.md): consolidated measured performance, accuracy and concurrency.
- [Single-stream results](docs/performance_benchmark.md), [concurrency results](docs/concurrency_results.md), [conditional sizing](docs/capacity_estimation.md).

Use root-level `benchmark_results/` reports for presentation. Dated folders preserve source runs, cancellations and snapshots; do not pool failed guardrail attempts into successful performance averages. No evidence was deleted during documentation cleanup.

## Troubleshooting

| Symptom | Check/action |
|---|---|
| Missing imports or wrong Python | Use `.\.venv\Scripts\python.exe`, confirm 3.12, then inspect `-m pip check`. Install requirements only into the intended environment. |
| PowerShell blocks activation/npm | Activation is optional; use the explicit Python executable and `npm.cmd`. No global policy change is needed. |
| Model unavailable / health 503 | Read startup log; check cached `Qwen/Qwen3-ASR-0.6B` and dependency versions. Startup never downloads weights automatically. Restart after fixing the cause. |
| WebSocket fails | Check ports 8000/5173, model readiness and Vite proxy configuration; restart Vite after env changes. |
| Invalid audio | Export WAV signed PCM16, mono, 16,000 Hz. Renaming the extension is insufficient. Run the sample validator. |
| High CPU / slow text | CPU work and three-second window collection both add latency. Avoid parallel benchmarks and unrelated heavy applications. Keep six threads and serialized inference for comparable results. |
| Low RAM / overload | Close unused applications. Respect RAM guards, session admission and bounded queues; stop the experiment rather than weakening safety limits. |
| Busy port | Reuse the correct existing process or stop your old server gracefully. Do not launch multiple model workers accidentally. |

## Limitations and remaining manual work

This is a local CPU prototype with independent, non-overlapping windows. Word boundaries lose context, recognition errors remain, and connected calls do not imply sustainable real-time processing. One short-recording call level consistently passed the selected criteria; two calls completed but were borderline. Conditional 50–1,000-call estimates are not measured deployments or minimum hardware requirements.

Fresh installation, manual real-browser interaction, visual diagram review and the personally spoken video remain separate checks. See the checklist. Keep confidential assignment material private; do not publish the assignment PDF, model cache, environment secrets or local dependencies.
