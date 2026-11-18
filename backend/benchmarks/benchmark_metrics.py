"""Benchmark latency calculations and local backend resource monitoring."""

import math
import statistics
import threading
from time import perf_counter

import psutil

MIB = 1024 ** 2


def inference_rtf(inference_seconds: float, audio_seconds: float) -> float:
    if not math.isfinite(inference_seconds) or inference_seconds < 0 or not math.isfinite(audio_seconds) or audio_seconds <= 0:
        raise ValueError("RTF requires finite nonnegative inference time and positive audio duration.")
    return inference_seconds / audio_seconds


def client_latencies(timing: dict) -> dict:
    """All timestamps originate in the same client's perf_counter clock."""
    start, last, end, final, completed = (timing[key] for key in (
        "stream_started_at", "last_audio_sent_at", "end_sent_at", "final_transcript_at", "completed_at"))
    first = timing.get("first_transcript_at")
    values = [start, last, end, final, completed] + ([] if first is None else [first])
    if not all(math.isfinite(x) for x in values) or not start <= last <= end <= final <= completed:
        raise ValueError("Inconsistent or non-finite client timestamps.")
    if first is not None and not start <= first <= final:
        raise ValueError("Invalid first transcript timestamp.")
    return {"time_to_first_transcript_seconds": None if first is None else first - start,
            "first_transcript_before_last_audio": None if first is None else first < last,
            "finalization_from_end_seconds": final - end,
            "finalization_from_last_audio_seconds": final - last,
            "total_end_to_end_seconds": completed - start,
            "audio_transmission_seconds": last - start}


def describe(values: list[float]) -> dict:
    return {"count": len(values), "mean": statistics.fmean(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None}


def aggregate_resources(samples: list[dict]) -> dict:
    """Interval-weighted CPU mean; arithmetic mean of observed RSS samples."""
    cpu = [s for s in samples if s.get("interval_seconds", 0) > 0 and s.get("process_cpu_percent") is not None]
    weight = sum(s["interval_seconds"] for s in cpu)

    def cpu_stats(key):
        return {"mean": sum(s[key] * s["interval_seconds"] for s in cpu) / weight if weight else None,
                "peak": max((s[key] for s in cpu), default=None)}

    rss = [s["process_rss_bytes"] / MIB for s in samples if s.get("process_rss_bytes") is not None]
    available = [s["available_ram_bytes"] / MIB for s in samples if s.get("available_ram_bytes") is not None]
    return {"system_cpu_percent": cpu_stats("system_cpu_percent"),
            "process_cpu_percent": cpu_stats("process_cpu_percent"),
            "process_rss_mib": describe(rss), "available_ram_mib": describe(available),
            "cpu_sample_count": len(cpu), "cpu_observed_seconds": weight}


class ResourceMonitor:
    """One low-frequency thread. RSS/CPU exclude child processes explicitly."""

    def __init__(self, pid: int, interval: float = 0.5):
        if not math.isfinite(interval) or interval < 0.1:
            raise ValueError("Monitoring interval must be at least 0.1 seconds.")
        self.process = psutil.Process(pid)
        self.created_at = self.process.create_time()
        self.interval = interval
        self.samples: list[dict] = []
        self.errors: list[str] = []
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def _snapshot(self, stage, started, previous=None):
        if not self.process.is_running() or self.process.create_time() != self.created_at:
            raise RuntimeError("Monitored backend process exited or PID was reused.")
        now = perf_counter()
        ram = psutil.virtual_memory()
        interval = 0 if previous is None else now - previous
        self.samples.append({"elapsed_seconds": now - started, "stage": stage,
                             "interval_seconds": interval,
                             "system_cpu_percent": None if previous is None else psutil.cpu_percent(None),
                             "process_cpu_percent": None if previous is None else self.process.cpu_percent(None),
                             "process_rss_bytes": self.process.memory_info().rss,
                             "total_ram_bytes": ram.total, "available_ram_bytes": ram.available,
                             "child_process_ids": [p.pid for p in self.process.children(recursive=True)]})
        return now

    def _run(self):
        started = perf_counter()
        try:
            # Prime/discard both counters in this same monitoring thread.
            psutil.cpu_percent(None)
            self.process.cpu_percent(None)
            previous = self._snapshot("before", started)
            self._ready.set()
            while not self._stop.wait(self.interval):
                previous = self._snapshot("during", started, previous)
            # Avoid an unstable tiny CPU interval; always retain an after RSS snapshot.
            previous = self._snapshot("after", started, previous if perf_counter() - previous >= 0.1 else None)
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            self._ready.set()

    def start(self):
        if self._thread is not None:
            raise RuntimeError("Monitor already started.")
        self._thread = threading.Thread(target=self._run, name="benchmark-resources", daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            raise RuntimeError("Resource monitor did not initialize.")
        if self.errors:
            raise RuntimeError(self.errors[0])

    def stop(self) -> dict:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                self.errors.append("Resource monitor did not stop within 10 seconds.")
        return {"pid": self.process.pid, "process_created_at": self.created_at,
                "interval_seconds": self.interval, "children_included": False,
                "samples": self.samples, "summary": aggregate_resources(self.samples), "errors": self.errors}
