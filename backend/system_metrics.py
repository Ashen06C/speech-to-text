"""Lightweight CPU sampling shared by all dashboard requests."""

import logging
import threading

import psutil

logger = logging.getLogger(__name__)


class CPUSampler:
    """Sample once per second; never wait for a CPU interval in an API request.

    Process percentages use psutil's raw scale: 100% is one logical CPU.
    Child processes are excluded. None means no valid sample is available yet.
    """

    def __init__(self):
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._sample: dict[str, float | None] = {
            "system_cpu_percent": None,
            "process_cpu_percent": None,
        }

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="dashboard-cpu", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def snapshot(self) -> dict[str, float | None]:
        with self._lock:
            return self._sample.copy()

    def _run(self):
        try:
            process = psutil.Process()
            # Discard initial counters. Keep all CPU reads on this same thread
            # and reuse Process so subsequent values measure a real interval.
            psutil.cpu_percent(interval=None)
            process.cpu_percent(interval=None)
            while not self._stop.wait(1.0):
                sample = {
                    "system_cpu_percent": psutil.cpu_percent(interval=None),
                    "process_cpu_percent": process.cpu_percent(interval=None),
                }
                with self._lock:
                    self._sample = sample
        except (psutil.Error, OSError):
            logger.exception("Dashboard CPU sampling is unavailable")
        finally:
            with self._lock:
                self._sample = dict.fromkeys(self._sample)
