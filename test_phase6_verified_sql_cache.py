import json
import unittest
from pathlib import Path

from core import phase6_orchestrator
from core.phase6_verified_sql_cache import (
    create_verified_sql_cache_tables,
    normalize_question,
    question_fingerprint,
    schema_fingerprint,
)


class VerifiedSqlCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(Path("data/schema_metadata.json").read_text())

    def test_question_normalization_handles_harmless_surface_difference(self):
        first = "What is total sales for HPCL?"
        second = "What is the total sales for HPCL"

        self.assertEqual(normalize_question(first), normalize_question(second))
        self.assertEqual(question_fingerprint(first), question_fingerprint(second))

    def test_schema_fingerprint_is_stable_for_key_order(self):
        self.assertEqual(
            schema_fingerprint({"tables": [{"table_name": "sales", "columns": ["a", "b"]}]}),
            schema_fingerprint({"tables": [{"columns": ["a", "b"], "table_name": "sales"}]}),
        )

    def test_verified_cache_schema_creation_is_migration_safe(self):
        class FakeCursor:
            def __init__(self):
                self.statements = []

            def execute(self, sql):
                self.statements.append(sql)

        cur = FakeCursor()
        create_verified_sql_cache_tables(cur)
        joined = "\n".join(cur.statements)

        self.assertIn("CREATE TABLE IF NOT EXISTS nlpsql_verified_sql_cache", joined)
        self.assertIn("CREATE TABLE IF NOT EXISTS nlpsql_rejected_sql", joined)
        self.assertIn("status TEXT NOT NULL DEFAULT 'candidate'", joined)
        self.assertIn("idx_nlpsql_verified_sql_cache_lookup", joined)

    def test_orchestrator_verified_sql_cache_skips_embedding_and_generation(self):
        originals = {
            "load_schema": phase6_orchestrator.load_schema,
            "get_verified_sql": phase6_orchestrator.get_verified_sql,
            "get_local_embedder": phase6_orchestrator.get_local_embedder,
            "retrieve_similar_examples": phase6_orchestrator.retrieve_similar_examples,
            "generate_sql": phase6_orchestrator.generate_sql,
            "validate_and_execute": phase6_orchestrator.validate_and_execute,
            "log_query": phase6_orchestrator.log_query,
            "store_candidate": phase6_orchestrator.store_candidate,
        }
        try:
            phase6_orchestrator.load_schema = lambda schema_path=phase6_orchestrator.SCHEMA_PATH: self.schema
            phase6_orchestrator.get_verified_sql = lambda *args, **kwargs: {
                "id": 7,
                "sql": "SELECT 1 AS answer;",
                "hit_count": 1,
            }
            phase6_orchestrator.get_local_embedder = lambda: (_ for _ in ()).throw(
                AssertionError("Embedding model should not load on verified cache hit")
            )
            phase6_orchestrator.retrieve_similar_examples = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("Retrieval should not run on verified cache hit")
            )
            phase6_orchestrator.generate_sql = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("Generation should not run on verified cache hit")
            )
            phase6_orchestrator.validate_and_execute = lambda **kwargs: {
                "success": True,
                "sql": kwargs["sql"],
                "columns": ["answer"],
                "rows": [[1]],
                "row_count": 1,
                "warnings": [],
                "error": None,
            }
            phase6_orchestrator.log_query = lambda *args, **kwargs: 42
            phase6_orchestrator.store_candidate = lambda *args, **kwargs: True

            result = phase6_orchestrator.run_pipeline(
                "What is the answer?",
                conn_str="unused",
                model="test-model",
            )
        finally:
            for name, value in originals.items():
                setattr(phase6_orchestrator, name, value)

        self.assertTrue(result["success"])
        self.assertEqual(result["generation_mode"], "verified_sql_cache_hit")
        self.assertEqual(result["review_status"], "verified")
        self.assertTrue(result["reviewed_sql_reused"])
        self.assertTrue(result["cache_hit"])
        self.assertEqual(result["rows"], [[1]])

    def test_orchestrator_repairs_rejected_regular_candidate_before_serving(self):
        originals = {
            "load_schema": phase6_orchestrator.load_schema,
            "get_verified_sql": phase6_orchestrator.get_verified_sql,
            "get_local_embedder": phase6_orchestrator.get_local_embedder,
            "retrieve_similar_examples": phase6_orchestrator.retrieve_similar_examples,
            "match_query_pattern": phase6_orchestrator.match_query_pattern,
            "route_question": phase6_orchestrator.route_question,
            "classify_complexity": phase6_orchestrator.classify_complexity,
            "retrieve_similar_schema": phase6_orchestrator.retrieve_similar_schema,
            "compress_generation_context": phase6_orchestrator.compress_generation_context,
            "generate_sql": phase6_orchestrator.generate_sql,
            "self_heal_sql": phase6_orchestrator.self_heal_sql,
            "validate_and_execute": phase6_orchestrator.validate_and_execute,
            "log_query": phase6_orchestrator.log_query,
            "store_candidate": phase6_orchestrator.store_candidate,
            "is_rejected_sql": phase6_orchestrator.is_rejected_sql,
            "_try_early_compiler_fast_path": phase6_orchestrator._try_early_compiler_fast_path,
        }
        rejected_sql = "SELECT SUM(mom_level.netweight_tmt) FROM mom_level;"
        repaired_sql = "SELECT SUM(industry_performance.netweight_tmt) FROM industry_performance;"
        import core.logical_planner
        original_classify = core.logical_planner.DeterministicIntentClassifier.classify

        try:
            core.logical_planner.DeterministicIntentClassifier.classify = lambda self, query: {"intent": "TOTAL_SALES", "confidence": 0.0}
            phase6_orchestrator.load_schema = lambda schema_path=phase6_orchestrator.SCHEMA_PATH: self.schema
            phase6_orchestrator.get_verified_sql = lambda *args, **kwargs: None
            phase6_orchestrator.get_local_embedder = lambda: None
            phase6_orchestrator.retrieve_similar_examples = lambda *args, **kwargs: []
            phase6_orchestrator.match_query_pattern = lambda *args, **kwargs: {"matched": False}
            phase6_orchestrator.route_question = lambda *args, **kwargs: {"complexity": "MEDIUM"}
            phase6_orchestrator.classify_complexity = lambda *args, **kwargs: "MEDIUM"
            phase6_orchestrator.retrieve_similar_schema = lambda *args, **kwargs: []
            phase6_orchestrator.compress_generation_context = lambda **kwargs: {
                "schema_context": [],
                "tables": ["industry_performance"],
                "schema_ddl": "CREATE TABLE industry_performance (netweight_tmt NUMERIC);",
            }
            phase6_orchestrator.generate_sql = lambda **kwargs: {
                "sql": rejected_sql,
                "generation_mode": "deterministic_compiler",
                "logical_plan": {"intent": "metric_query"},
            }
            phase6_orchestrator.self_heal_sql = lambda *args, **kwargs: repaired_sql
            phase6_orchestrator.is_rejected_sql = lambda conn_str, question, sql: sql == rejected_sql
            phase6_orchestrator._try_early_compiler_fast_path = lambda *args, **kwargs: None
            phase6_orchestrator.validate_and_execute = lambda **kwargs: {
                "success": True,
                "sql": kwargs["sql"],
                "columns": ["sum"],
                "rows": [[1]],
                "row_count": 1,
                "warnings": [],
                "error": None,
                "healed": False,
            }
            phase6_orchestrator.log_query = lambda *args, **kwargs: 43
            phase6_orchestrator.store_candidate = lambda *args, **kwargs: True

            result = phase6_orchestrator.run_pipeline(
                "What is the actual sales for SMA?",
                conn_str="unused",
                model="test-model",
            )
        finally:
            core.logical_planner.DeterministicIntentClassifier.classify = original_classify
            for name, value in originals.items():
                setattr(phase6_orchestrator, name, value)

        self.assertTrue(result["success"])
        self.assertEqual(result["generation_mode"], "rejected_sql_llm_repair")
        self.assertEqual(result["sql"], repaired_sql)

    def test_fast_llm_generation_retries_with_small_model_after_primary_stall(self):
        original_generate = phase6_orchestrator.generate_sql
        original_fast_model = phase6_orchestrator.FAST_LLM_MODEL
        original_fast_timeout = phase6_orchestrator.FAST_LLM_TIMEOUT_SECONDS
        calls = []
        try:
            phase6_orchestrator.FAST_LLM_MODEL = "small-local-model"
            phase6_orchestrator.FAST_LLM_TIMEOUT_SECONDS = 2

            def fake_generate_sql(**kwargs):
                calls.append(kwargs)
                return {"sql": "SELECT 1;", "generation_mode": "planned_llm_fallback"}

            phase6_orchestrator.generate_sql = fake_generate_sql
            result = phase6_orchestrator._try_fast_llm_generation(
                question="Show a supported metric",
                schema_context=[],
                few_shot_examples=[],
                relevant_ddl="",
                schema_metadata=self.schema,
                entity_analysis={"matches": []},
                primary_model="large-local-model",
                request_id="test-request",
            )
        finally:
            phase6_orchestrator.generate_sql = original_generate
            phase6_orchestrator.FAST_LLM_MODEL = original_fast_model
            phase6_orchestrator.FAST_LLM_TIMEOUT_SECONDS = original_fast_timeout

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "small-local-model")
        self.assertGreater(calls[0]["deadline"], 0)
        self.assertEqual(result["generation_mode"], "fast_llm_fallback:planned_llm_fallback")
        self.assertEqual(result["primary_model"], "large-local-model")

    def test_generation_failure_returns_clarification_without_internal_timeout_message(self):
        result = phase6_orchestrator._generation_failure_response(
            question="Show sales growth",
            error=TimeoutError("internal deadline exhausted"),
            start_perf=0.0,
            request_id="test-request",
            schema_metadata=self.schema,
            entity_analysis={"matches": [], "unresolved": []},
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["needs_clarification"])
        self.assertEqual(result["generation_mode"], "needs_clarification")
        self.assertIsNone(result["error"])
        self.assertNotIn("latency budget", result["assistant_message"].lower())
        self.assertNotIn("verified query pattern", result["assistant_message"].lower())

    def test_generation_failure_names_unresolved_dataset_entity(self):
        result = phase6_orchestrator._generation_failure_response(
            question="Show sales for UNKNOWN company",
            error=TimeoutError("internal deadline exhausted"),
            start_perf=0.0,
            request_id="test-request",
            schema_metadata=self.schema,
            entity_analysis={
                "matches": [],
                "unresolved": [{"type": "company", "value": "unknown"}],
            },
        )

        self.assertIn("company: unknown", result["assistant_message"])


if __name__ == "__main__":
    unittest.main()
