# Capacity estimation — ESTIMATED scenarios only

Status: **ESTIMATED — CONDITIONAL**

No 50–1,000-call workload was run. Blank sizing values mean measurements or explicit server calibration are insufficient.

Highest measured short-recording level meeting criteria: 1

Source observations: `D:\speech-to-text\benchmark_results\concurrency_results.json`

Server assumptions: `{'vcpu': 12, 'ram_gib': 16.0, 'capacity_factor': 1.0, 'replicas': 1, 'headroom': 0.25, 'extra_stream_mib': 64, 'os_reserve_gib': 2, 'headroom_basis': 'inference_time'}`

| Call Legs | Estimated vCPU | Estimated RAM GiB | Estimated Servers | Reason / Assumption |
|---|---:|---:|---:|---|
| 50 | 600 | 800.0 | 50 | 1 effective whole calls/server; explicit capacity factor 1.0; 25% headroom applied once to inference_time; 1 model replicas/server. Uncalibrated server hypothesis. |
| 100 | 1200 | 1600.0 | 100 | 1 effective whole calls/server; explicit capacity factor 1.0; 25% headroom applied once to inference_time; 1 model replicas/server. Uncalibrated server hypothesis. |
| 200 | 2400 | 3200.0 | 200 | 1 effective whole calls/server; explicit capacity factor 1.0; 25% headroom applied once to inference_time; 1 model replicas/server. Uncalibrated server hypothesis. |
| 500 | 6000 | 8000.0 | 500 | 1 effective whole calls/server; explicit capacity factor 1.0; 25% headroom applied once to inference_time; 1 model replicas/server. Uncalibrated server hypothesis. |
| 1,000 | 12000 | 16000.0 | 1000 | 1 effective whole calls/server; explicit capacity factor 1.0; 25% headroom applied once to inference_time; 1 model replicas/server. Uncalibrated server hypothesis. |

## This scenario

- Worst observed passing per-call inference RTF: 0.555341.
- Calls allowed by the inference-time budget after headroom: 1.351 before rounding/bounds.
- Measured call bound under the explicit server hypothesis: 1.000.
- Effective whole calls per assumed server: 1.
- Fixed backend/working-memory baseline plus OS reserve: 6.158 GiB.

The capacity factor of 1 means the assumed production worker must run this workload no slower than the measured worker. It is a condition to validate, not evidence that laptop logical CPUs and cloud vCPU are equivalent. These are conservative planning budgets, not proven minimum requirements or a hardware purchase recommendation.

## Formulas

1. Assumed calls/server = min(measured passing calls × explicit server-capacity factor × replicas, configured session limit × replicas).
2. Apply headroom once. In call_slots mode: floor(assumed calls/server × (1 − headroom)). In inference_time mode: floor(min(assumed calls/server, capacity factor × replicas × (1 − headroom) / worst passing per-call RTF)).
3. Fixed memory = passing one-call peak backend RSS × model replicas + assumed OS/services reserve. This measured RSS includes runtime and working memory, not just model weights.
4. Extra calls consume assumed per-stream overhead (including buffers/queues). Memory budget limits effective whole calls/server; if one call cannot fit, no numerical scenario is issued.
5. Servers = ceil(target / effective calls/server). Aggregate vCPU/RAM = servers × assumed server vCPU/RAM.

Headroom is not applied a second time in server counting. Existing latency/operating criteria are selection rules, not an additional numerical CPU multiplier. Inference-time headroom reserves model execution wall time, not a percentage of physical CPU utilization. It does not claim more calls than the passing measured bound under the explicit server hypothesis. Default 64 MiB extra-stream overhead and 2 GiB OS reserve are planning assumptions, not measured isolated allocations. Per-worker model weights are shared between streams; replicas multiply model memory.

## Limitations

- ESTIMATED, low-confidence conditional scenario; no production server was benchmarked.
- 12 laptop logical processors are not proof of equivalence to 12 cloud vCPU.
- CPU, language mix, continuous-call duration, queues, power, and thermal behavior may scale nonlinearly.
- Multiple replicas require separate model/runtime memory and CPU calibration; memory is not multiplied per call within one replica.
