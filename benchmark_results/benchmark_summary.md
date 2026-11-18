# CPU WebSocket benchmark results

Recorded: 2026-11-18T18:58:00.487838+05:30

Only executed runs appear below. Failed runs are retained and excluded from numerical summaries. Warm-ups, if requested, are stored separately in JSON and excluded from measured results.

## System configuration

- cpu_model: 13th Gen Intel(R) Core(TM) i5-13420H
- cpu_physical_cores: 8
- cpu_logical_processors: 12
- operating_system: Windows-11-10.0.26200-SP0
- python_version: 3.12.10
- model: Qwen3-ASR-0.6B
- device: cpu
- runtime: PyTorch CPU / Transformers
- backend_pid: 26412
- configured_cpu_threads: 6
- torch_num_threads: NOT MEASURED
- thread_configuration_source: Backend process environment or source defaults; live torch count unavailable on this already-running server
- window_ms: 3000
- chunk_duration_ms: 500
- model_load_time_seconds: NOT MEASURED
- Total system RAM: 15.669 GiB
- Local package versions: `{'torch': '2.15.0+cpu', 'qwen-asr': '0.0.6', 'psutil': '7.2.2', 'jiwer': '4.0.0', 'websockets': '17.4'}`

Model loading is separate from inference. An already-running older backend cannot report a historical load time or live torch thread count; null values remain NOT MEASURED. A configuration inferred from its environment/source is labeled, not claimed as a live torch measurement.

## Individual measured runs

CPU columns: interval-weighted mean / observed peak. Process CPU uses one logical CPU as 100% and may exceed 100%; system CPU is for the entire machine (0–100%). RSS excludes children.

| Language/run | Audio s | First text s | End→final s | End-to-end s | Inference s | Queue s | RTF | System CPU % | Backend CPU % | RSS peak MiB | WER/CER |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| English/1 | 12.153 | 8.871 | 2.208 | 14.366 | 11.339 | 6.450 | 0.933 | 55.421 / 90.300 | 374.486 / 600.700 | 3094.461 | WER 20.000% |
| English/2 | 12.153 | 4.631 | 1.748 | 13.906 | 6.566 | 1.139 | 0.540 | 46.719 / 78.100 | 304.369 / 609.700 | 3095.973 | WER 20.000% |
| English/3 | 12.153 | 4.605 | 1.806 | 13.963 | 6.698 | 1.177 | 0.551 | 39.837 / 80.100 | 307.177 / 603.800 | 3096.453 | WER 20.000% |
| Chinese/1 | 21.589 | 4.246 | 1.013 | 22.617 | 9.116 | 0.585 | 0.422 | 44.645 / 90.400 | 266.428 / 614.700 | 3098.055 | CER 21.154% |
| Chinese/2 | 21.589 | 4.247 | 0.991 | 22.588 | 8.841 | 0.542 | 0.410 | 39.295 / 73.900 | 261.275 / 615.600 | 3098.398 | CER 21.154% |
| Chinese/3 | 21.589 | 4.370 | 1.003 | 22.606 | 9.499 | 0.573 | 0.440 | 43.381 / 83.500 | 271.581 / 613.200 | 3098.332 | CER 21.154% |
| Indonesian/1 | 17.832 | 4.438 | 1.241 | 19.077 | 9.021 | 0.001 | 0.506 | 44.961 / 78.700 | 305.014 / 617.700 | 3098.578 | WER 18.519% |
| Indonesian/2 | 17.832 | 4.386 | 1.645 | 19.489 | 10.355 | 0.002 | 0.581 | 60.290 / 97.300 | 322.597 / 602.100 | 3094.402 | WER 18.519% |
| Indonesian/3 | 17.832 | 4.447 | 1.431 | 19.277 | 9.901 | 0.002 | 0.555 | 49.495 / 81.500 | 320.154 / 611.700 | 3102.496 | WER 18.519% |

## Repeated-run summary

Latency columns show mean (minimum–maximum); means across runs give each run equal weight.

| Language | Completed/failed | First text s | End→final s | End-to-end s | Mean RTF | Mean system CPU % | Mean backend CPU % | Peak RSS MiB | Mean WER/CER |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| English | 3/0 | 6.036 (4.605–8.871) | 1.921 (1.748–2.208) | 14.078 (13.906–14.366) | 0.675 | 47.326 | 328.677 | 3096.453 | WER 20.000% |
| Chinese | 3/0 | 4.288 (4.246–4.370) | 1.003 (0.991–1.013) | 22.604 (22.588–22.617) | 0.424 | 42.441 | 266.428 | 3098.398 | CER 21.154% |
| Indonesian | 3/0 | 4.423 (4.386–4.447) | 1.439 (1.241–1.645) | 19.281 (19.077–19.489) | 0.547 | 51.582 | 315.921 | 3102.496 | WER 18.519% |

## Observations

- English: mean inference RTF 0.675 is faster than audio duration. First nonempty text arrived before the last chunk in 3/3 runs.
- Chinese: mean inference RTF 0.424 is faster than audio duration. First nonempty text arrived before the last chunk in 3/3 runs.
- Indonesian: mean inference RTF 0.547 is faster than audio duration. First nonempty text arrived before the last chunk in 3/3 runs.
- Indonesian had the highest mean total inference time among these recordings. Recording durations/content differ, so this does not establish a general language-speed ranking.
- CPU monitoring shows utilization, but this experiment alone does not establish whether CPU, memory pressure, or another resource caused a bottleneck. Background applications also contribute to system CPU.
- RSS snapshots below show observed variation, not a memory-leak diagnosis. Windows residency can change with paging and working-set trimming.

### Backend memory before and after each stream

| Language/run | Before MiB | After MiB | Minimum RSS MiB | Mean RSS MiB | Peak RSS MiB | Minimum available system MiB |
|---|---:|---:|---:|---:|---:|---:|
| English/1 | 39.629 | 3034.473 | 39.629 | 1853.415 | 3094.461 | 88.332 |
| English/2 | 3034.473 | 3038.352 | 3034.473 | 3052.566 | 3095.973 | 356.543 |
| English/3 | 3038.352 | 3037.898 | 3037.891 | 3052.227 | 3096.453 | 377.938 |
| Chinese/1 | 3037.898 | 3053.625 | 3037.898 | 3056.864 | 3098.055 | 629.051 |
| Chinese/2 | 3053.625 | 3040.023 | 3040.023 | 3057.818 | 3098.398 | 1963.801 |
| Chinese/3 | 3040.023 | 3039.070 | 3039.070 | 3058.098 | 3098.332 | 1939.883 |
| Indonesian/1 | 3039.070 | 3049.492 | 3039.070 | 3055.701 | 3098.578 | 1936.668 |
| Indonesian/2 | 3049.492 | 3080.223 | 3046.539 | 3065.728 | 3094.402 | 1664.113 |
| Indonesian/3 | 3080.223 | 3050.934 | 3047.570 | 3065.805 | 3102.496 | 1798.426 |

## Accuracy evidence

Raw references remain unchanged. See benchmark_methodology.md for normalization. Error rates may exceed 100%; they are not converted into 100% minus error rate.

### English run 1

Reference: Hello, this is a test of the speech recognition system. I am speaking clearly so we can measure how quickly the words appear on the screen. Thank you for listening.

Generated: Hello. This is my test on the speech recognition. I am speaking clearly. So. We can measure how quickly the words appear on the screen. Green. Thank you for listening. I'm sorry.

WER: 20.000%; substitutions=2, deletions=1, insertions=3, reference units=30.

### English run 2

Reference: Hello, this is a test of the speech recognition system. I am speaking clearly so we can measure how quickly the words appear on the screen. Thank you for listening.

Generated: Hello. This is my test on the speech recognition. I am speaking clearly. So. We can measure how quickly the words appear on the screen. Green. Thank you for listening. I'm sorry.

WER: 20.000%; substitutions=2, deletions=1, insertions=3, reference units=30.

### English run 3

Reference: Hello, this is a test of the speech recognition system. I am speaking clearly so we can measure how quickly the words appear on the screen. Thank you for listening.

Generated: Hello. This is my test on the speech recognition. I am speaking clearly. So. We can measure how quickly the words appear on the screen. Green. Thank you for listening. I'm sorry.

WER: 20.000%; substitutions=2, deletions=1, insertions=3, reference units=30.

### Chinese run 1

Reference: 你好，欢迎参加今天的语音识别测试。现在我正在说普通话。请检查屏幕上的文字是否正确。我们还要测量系统的响应时间。谢谢。

Generated: 你好，欢迎参加亲子。边的语音识别测试。现在我正在，我不懂。哇，请检查屏幕上。看的文字是否正确？我们还要车辆系统着。响应时间。谢谢。。

CER: 21.154%; substitutions=9, deletions=0, insertions=2, reference units=52.

### Chinese run 2

Reference: 你好，欢迎参加今天的语音识别测试。现在我正在说普通话。请检查屏幕上的文字是否正确。我们还要测量系统的响应时间。谢谢。

Generated: 你好，欢迎参加亲子。边的语音识别测试。现在我正在，我不懂。哇，请检查屏幕上。看的文字是否正确？我们还要车辆系统着。响应时间。谢谢。。

CER: 21.154%; substitutions=9, deletions=0, insertions=2, reference units=52.

### Chinese run 3

Reference: 你好，欢迎参加今天的语音识别测试。现在我正在说普通话。请检查屏幕上的文字是否正确。我们还要测量系统的响应时间。谢谢。

Generated: 你好，欢迎参加亲子。边的语音识别测试。现在我正在，我不懂。哇，请检查屏幕上。看的文字是否正确？我们还要车辆系统着。响应时间。谢谢。。

CER: 21.154%; substitutions=9, deletions=0, insertions=2, reference units=52.

### Indonesian run 1

Reference: Halo, ini adalah pengujian sistem pengenalan suara. Saya berbicara dengan jelas dalam bahasa Indonesia. Kami ingin mengetahui seberapa cepat teks muncul di layar. Terima kasih sudah mendengarkan.

Generated: halo ini adalah pengujian sistem nyaran suara saya berbicara Benggan jelas salam bahasa Indonesia. Kami ingin mengetahui seberapa Pateks muncul di layar terima. kasih sudah mendengarkan

WER: 18.519%; substitutions=4, deletions=1, insertions=0, reference units=27.

### Indonesian run 2

Reference: Halo, ini adalah pengujian sistem pengenalan suara. Saya berbicara dengan jelas dalam bahasa Indonesia. Kami ingin mengetahui seberapa cepat teks muncul di layar. Terima kasih sudah mendengarkan.

Generated: halo ini adalah pengujian sistem nyaran suara saya berbicara Benggan jelas salam bahasa Indonesia. Kami ingin mengetahui seberapa Pateks muncul di layar terima. kasih sudah mendengarkan

WER: 18.519%; substitutions=4, deletions=1, insertions=0, reference units=27.

### Indonesian run 3

Reference: Halo, ini adalah pengujian sistem pengenalan suara. Saya berbicara dengan jelas dalam bahasa Indonesia. Kami ingin mengetahui seberapa cepat teks muncul di layar. Terima kasih sudah mendengarkan.

Generated: halo ini adalah pengujian sistem nyaran suara saya berbicara Benggan jelas salam bahasa Indonesia. Kami ingin mengetahui seberapa Pateks muncul di layar terima. kasih sudah mendengarkan

WER: 18.519%; substitutions=4, deletions=1, insertions=0, reference units=27.

## Failures

No recorded setup/session failures.

## Limitations

- Single Windows laptop, CPU only; thermal state, power mode, paging, other applications, and first-inference/cache effects can change results.
- Three short recordings; repeated runs do not establish population-level language accuracy or performance.
- Sequential non-overlapping windows are progressive inference, not native Qwen streaming. Boundary cuts and tiny residual windows can harm recognition.
- One stream at a time. No capacity, concurrent-call, infrastructure, quantization, or deployment conclusions.
- Resource sampling includes connection/validation setup around the stream, excludes child-process RSS/CPU, and may miss sub-interval peaks. RSS includes the whole backend, not model weights alone.
- Inference RTF excludes queue/network/transmission time. Client durations never subtract backend timestamps.
- Optional warm-ups and historical model loading are reported separately. Measurements missing from an older running backend remain unavailable.
