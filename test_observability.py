import unittest

from core.observability import (
    PROMETHEUS_AVAILABLE,
    observe_retrieval,
    observed_stage,
    prometheus_metrics,
    record_cache_event,
    record_pipeline_result,
    text_fingerprint,
)
from test_schema_fixtures import validation_schema
from core import phase4_validator


class ObservabilityTests(unittest.TestCase):
    def test_stage_timer_records_elapsed_time(self):
        with observed_stage("test.stage") as timer:
            value = 1 + 1

        self.assertEqual(value, 2)
        self.assertGreaterEqual(timer.elapsed_ms, 0)
        self.assertEqual(timer.outcome, "success")

    def test_prometheus_metrics_are_exportable_or_noop(self):
        observe_retrieval("examples", 0.001, 3)
        record_cache_event("query_pattern", "hit")
        record_pipeline_result(0.002, True, "unit_test")

        payload = prometheus_metrics()

        self.assertIsInstance(payload, bytes)
        if PROMETHEUS_AVAILABLE:
            self.assertIn(b"nlpsql_retrieval_duration_seconds", payload)
            self.assertIn(b"nlpsql_cache_events_total", payload)
        else:
            self.assertIn(b"prometheus_client is not installed", payload)

    def test_text_fingerprint_is_stable_and_short(self):
        self.assertEqual(text_fingerprint("Show Sales"), text_fingerprint(" show sales "))
        self.assertEqual(len(text_fingerprint("Show Sales")), 16)

    def test_validation_result_includes_timing_breakdown(self):
        original_explain = phase4_validator.explain_query
        original_execute = phase4_validator.execute_query
        try:
            phase4_validator.explain_query = lambda sql, conn_str: (True, "ok", [])
            phase4_validator.execute_query = lambda sql, conn_str, timeout_ms=30000: {
                "success": True,
                "columns": ["id"],
                "rows": [[1]],
                "row_count": 1,
                "execution_time_ms": 1,
                "error": None,
            }

            result = phase4_validator.validate_and_execute(
                "SELECT m60_level.engine_id FROM m60_level;",
                validation_schema(),
                "unused",
            )
        finally:
            phase4_validator.explain_query = original_explain
            phase4_validator.execute_query = original_execute

        self.assertTrue(result["success"])
        self.assertIn("timings", result)
        self.assertIn("ast_validation_ms", result["timings"])
        self.assertIn("repair_ms", result["timings"])


if __name__ == "__main__":
    unittest.main()
