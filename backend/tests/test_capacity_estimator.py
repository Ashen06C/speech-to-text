"""Known synthetic calibration cases; none are measured capacity claims."""

import unittest

from backend.benchmarks.capacity_estimator import ServerAssumptions, estimate, render_estimates


def calibration(calls=2):
    return {"rounds": [{"synthetic": True}], "backend_configuration": {"max_sessions": 4}, "levels": [
        {"concurrency": 1, "meets_criteria": True, "all_runs_successful": True,
         "resources": {"process_rss_mib": {"max": 3072}}},
        {"concurrency": calls, "meets_criteria": True, "all_runs_successful": True,
         "resources": {"process_rss_mib": {"max": 3100}}}]}


class CapacityTests(unittest.TestCase):
    def test_missing_measurements_are_a_template(self):
        result = estimate(None, ServerAssumptions())
        self.assertEqual(result["status"], "NOT ESTIMATED")
        self.assertEqual(len(result["rows"]), 5)
        self.assertTrue(all(r["estimated_servers"] is None for r in result["rows"]))
        self.assertIn("Not estimated", render_estimates(result))

    def test_no_passing_level_does_not_prove_zero_capacity(self):
        data = calibration()
        for row in data["levels"]:
            row.update(meets_criteria=False, all_runs_successful=False)
        result = estimate(data, ServerAssumptions(capacity_factor=1))
        self.assertIsNone(result["measured_streams"])
        self.assertIn("does not prove zero", result["rows"][0]["reason"])

    def test_explicit_uncalibrated_server_hypothesis_required(self):
        result = estimate(calibration(), ServerAssumptions())
        self.assertEqual(result["status"], "NOT ESTIMATED")
        self.assertIn("capacity-factor", result["rows"][0]["reason"])

    def test_server_vcpu_ram_calculations_and_headroom_once(self):
        result = estimate(calibration(), ServerAssumptions(capacity_factor=1, headroom=.25))
        # 2 measured calls * .75 => floor(1.5) => one whole call/server.
        self.assertEqual(result["effective_calls_per_server"], 1)
        first = result["rows"][0]
        self.assertEqual(first["estimated_servers"], 50)
        self.assertEqual(first["estimated_vcpu"], 600)
        self.assertEqual(first["estimated_ram_gib"], 800)

    def test_one_call_fractional_capacity_not_spread_across_servers(self):
        result = estimate(calibration(1), ServerAssumptions(capacity_factor=1, headroom=.25))
        self.assertEqual(result["effective_calls_per_server"], 0)
        self.assertIsNone(result["rows"][0]["estimated_servers"])

    def test_memory_budget_restricts_capacity(self):
        result = estimate(calibration(), ServerAssumptions(capacity_factor=2, headroom=0, ram_gib=5.1, extra_stream_mib=64))
        self.assertEqual(result["effective_calls_per_server"], 2)
        self.assertAlmostEqual(result["fixed_process_and_os_gib"], 5)

    def test_model_memory_multiplied_by_replicas_not_calls(self):
        single = estimate(calibration(), ServerAssumptions(capacity_factor=1, headroom=0))
        replicas = estimate(calibration(), ServerAssumptions(capacity_factor=1, headroom=0, replicas=2))
        self.assertEqual(single["fixed_process_and_os_gib"], 5)
        self.assertEqual(replicas["fixed_process_and_os_gib"], 8)

    def test_nonlinear_scaling_and_vcpu_warnings(self):
        warnings = " ".join(estimate(calibration(), ServerAssumptions(capacity_factor=1))["warnings"])
        self.assertIn("nonlinearly", warnings)
        self.assertIn("not proof", warnings)

    def test_invalid_assumptions(self):
        for values in ({"headroom": 1}, {"vcpu": 0}, {"capacity_factor": float("inf")}, {"ram_gib": -1}):
            with self.assertRaises(ValueError):
                ServerAssumptions(**values)

    def test_incomplete_measurement_input_returns_template(self):
        for data in ([], {}, {"rounds": [1], "levels": [{}]}):
            result = estimate(data, ServerAssumptions(capacity_factor=1))
            self.assertTrue(all(r["estimated_servers"] is None for r in result["rows"]))

    def test_inference_headroom_can_retain_one_call_with_spare_execution_time(self):
        data = calibration(1)
        data["rounds"] = [{"concurrency": 1, "acceptance": {"meets_criteria": True}, "calls": [
            {"status": "COMPLETED", "inference_seconds": 5, "audio_duration_seconds": 10}]}]
        result = estimate(data, ServerAssumptions(capacity_factor=1, headroom=.25, headroom_basis="inference_time"))
        self.assertEqual(result["effective_calls_per_server"], 1)
        self.assertEqual(result["worst_measured_passing_call_rtf"], .5)
        self.assertEqual(result["inference_time_capacity_after_headroom"], 1.5)
        self.assertEqual(result["rows"][0]["estimated_servers"], 50)

    def test_inference_headroom_rejects_busy_or_unmeasured_worker(self):
        data = calibration(1)
        options = ServerAssumptions(capacity_factor=1, headroom=.25, headroom_basis="inference_time")
        self.assertEqual(estimate(data, options)["status"], "NOT ESTIMATED")
        data["rounds"] = [{"concurrency": 1, "acceptance": {"meets_criteria": True}, "calls": [
            {"status": "COMPLETED", "inference_seconds": 9, "audio_duration_seconds": 10}]}]
        self.assertEqual(estimate(data, options)["status"], "NOT ESTIMATED")

    def test_inference_budget_never_extrapolates_beyond_measured_call_bound(self):
        data = calibration(1)
        data["rounds"] = [{"concurrency": 1, "acceptance": {"meets_criteria": True}, "calls": [
            {"status": "COMPLETED", "inference_seconds": 1, "audio_duration_seconds": 10}]}]
        result = estimate(data, ServerAssumptions(capacity_factor=1, headroom_basis="inference_time"))
        self.assertEqual(result["effective_calls_per_server"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
