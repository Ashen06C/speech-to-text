"""CPU counters are primed, shared, and stopped without loading ASR."""

import unittest
from unittest.mock import Mock, patch

import psutil

from backend.system_metrics import CPUSampler


class CPUSamplerTests(unittest.TestCase):
    def test_first_sample_uses_primed_counters_and_preserves_multicore_scale(self):
        sampler = CPUSampler()
        self.assertIsNone(sampler.snapshot()["process_cpu_percent"])
        process = Mock()
        process.cpu_percent.side_effect = [0.0, 320.5]

        def wait(_interval):
            if process.cpu_percent.call_count == 1:
                return False
            self.assertEqual(sampler.snapshot(), {
                "system_cpu_percent": 48.2, "process_cpu_percent": 320.5,
            })
            # A caller cannot mutate the shared sample.
            sampler.snapshot()["process_cpu_percent"] = -1
            self.assertEqual(sampler.snapshot()["process_cpu_percent"], 320.5)
            return True

        with patch("backend.system_metrics.psutil.Process", return_value=process), \
             patch("backend.system_metrics.psutil.cpu_percent", side_effect=[0.0, 48.2]) as system_cpu, \
             patch.object(sampler._stop, "wait", side_effect=wait):
            sampler._run()
        self.assertEqual(system_cpu.call_count, 2)
        self.assertIsNone(sampler.snapshot()["process_cpu_percent"])

    def test_start_stop_cleans_up_thread(self):
        sampler = CPUSampler()
        sampler.start()
        sampler.stop()
        self.assertFalse(sampler._thread.is_alive())
        self.assertIsNone(sampler.snapshot()["process_cpu_percent"])

    def test_counter_failure_remains_unavailable(self):
        sampler = CPUSampler()
        with patch("backend.system_metrics.psutil.Process", side_effect=psutil.AccessDenied()), \
             self.assertLogs("backend.system_metrics", level="ERROR"):
            sampler._run()
        self.assertIsNone(sampler.snapshot()["system_cpu_percent"])


if __name__ == "__main__":
    unittest.main()
