import unittest

from core import phase7_evaluation


class EvaluationLoopTests(unittest.TestCase):
    def test_evaluation_tracks_accuracy_and_latency_separately(self):
        original_connect = phase7_evaluation.psycopg2.connect

        class FakeCursor:
            def execute(self, _sql):
                pass

            def fetchall(self):
                return [(1,)]

            def close(self):
                pass

        class FakeConnection:
            def cursor(self):
                return FakeCursor()

            def close(self):
                pass

        try:
            phase7_evaluation.psycopg2.connect = lambda _dsn: FakeConnection()
            report = phase7_evaluation.run_evaluation(
                "unused",
                [{"question": "count", "gold_sql": "SELECT 1", "max_latency_ms": 100}],
                lambda _question: {
                    "sql": "SELECT 1",
                    "rows": [[1]],
                    "execution_time_ms": 150,
                    "generation_mode": "query_pattern_template",
                },
            )
        finally:
            phase7_evaluation.psycopg2.connect = original_connect

        result = report["results"][0]
        self.assertTrue(result["accuracy_passed"])
        self.assertFalse(result["latency_passed"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["generation_mode"], "query_pattern_template")


if __name__ == "__main__":
    unittest.main()
