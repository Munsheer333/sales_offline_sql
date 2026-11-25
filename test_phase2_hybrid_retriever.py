import inspect
import unittest

from core.phase2_hybrid_retriever import (
    create_full_text_indexes,
    expand_business_synonyms,
    fuse_retrieval_results,
    retrieve_hybrid_examples_async,
    retrieve_hybrid_schema_async,
)
from core.phase2_cross_encoder_reranker import adaptive_cross_encoder_rerank


class HybridRetrieverTests(unittest.TestCase):
    def test_business_synonym_expansion_adds_schema_terms(self):
        expanded = expand_business_synonyms(
            "Show company market share sales for AP in current fiscal year"
        ).lower()

        self.assertIn("comname", expanded)
        self.assertIn("netweight_tmt", expanded)
        self.assertIn("fiscal_year", expanded)
        self.assertIn("andhra pradesh", expanded)

    def test_synonyms_loaded_from_json(self):
        expanded = expand_business_synonyms("growth sales market share").lower()
        self.assertIn("increase", expanded)
        self.assertIn("improvement", expanded)
        self.assertIn("gain", expanded)
        self.assertIn("volume", expanded)
        self.assertIn("business", expanded)
        self.assertIn("turnover", expanded)
        self.assertIn("contribution", expanded)
        self.assertIn("share", expanded)
        self.assertIn("contribution %", expanded)


    def test_fusion_preserves_vector_similarity_and_adds_confidence(self):
        vector_rows = [
            {
                "id": 1,
                "table": "mom_level",
                "column": "netweight_tmt",
                "description": "actual sales volume",
                "similarity": 0.72,
            },
            {
                "id": 2,
                "table": "industry_performance",
                "column": "comname",
                "description": "company name",
                "similarity": 0.99,
            },
        ]
        text_rows = [
            {
                "id": 1,
                "table": "mom_level",
                "column": "netweight_tmt",
                "description": "actual sales volume",
                "text_score": 8.0,
            }
        ]

        results = fuse_retrieval_results(
            question="actual sales company",
            vector_rows=vector_rows,
            text_rows=text_rows,
            top_k=2,
            result_type="schema",
            expanded_query="actual sales company netweight_tmt comname",
        )

        self.assertEqual(results[0]["table"], "industry_performance")
        self.assertEqual(results[0]["similarity"], 0.99)
        self.assertIn("hybrid_score", results[0])
        self.assertIn("retrieval_confidence", results[0])
        self.assertGreater(results[1]["text_score"], 0.0)
        self.assertEqual(results[1]["similarity"], 0.72)

    def test_text_only_example_does_not_fake_vector_similarity(self):
        results = fuse_retrieval_results(
            question="target quantity north zone",
            vector_rows=[],
            text_rows=[
                {
                    "id": 10,
                    "question": "Get target quantity in North zone",
                    "sql": "SELECT 1;",
                    "complexity": "MEDIUM",
                    "text_score": 4.0,
                }
            ],
            top_k=1,
            result_type="example",
            expanded_query="target quantity north zone target_qty_kl zone_name",
        )

        self.assertEqual(results[0]["similarity"], 0.0)
        self.assertEqual(results[0]["retrieval_method"], "full_text")
        self.assertGreater(results[0]["retrieval_confidence"], 0.0)

    def test_full_text_index_setup_is_migration_safe(self):
        class FakeCursor:
            def __init__(self):
                self.sql = []

            def execute(self, statement):
                self.sql.append(statement)

        cur = FakeCursor()
        create_full_text_indexes(cur)
        joined_sql = "\n".join(cur.sql)

        self.assertIn("CREATE INDEX IF NOT EXISTS idx_schema_fts", joined_sql)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_pairs_fts", joined_sql)
        self.assertIn("USING GIN", joined_sql)

    def test_async_entrypoints_exist(self):
        self.assertTrue(inspect.iscoroutinefunction(retrieve_hybrid_schema_async))
        self.assertTrue(inspect.iscoroutinefunction(retrieve_hybrid_examples_async))

    def test_cross_encoder_is_skipped_for_high_confidence_retrieval(self):
        calls = []
        results = adaptive_cross_encoder_rerank(
            "company sales",
            [
                {"table": "industry_performance", "column": "comname", "retrieval_confidence": 0.90, "rerank_score": 0.9},
                {"table": "mom_level", "column": "netweight_tmt", "retrieval_confidence": 0.40, "rerank_score": 0.4},
            ],
            "schema",
            scorer=lambda pairs: calls.append(pairs) or [0.1, 0.9],
        )

        self.assertEqual(calls, [])
        self.assertFalse(results[0]["reranker_applied"])
        self.assertEqual(results[0]["reranker_reason"], "high_confidence")

    def test_cross_encoder_reranks_only_low_confidence_candidates(self):
        results = adaptive_cross_encoder_rerank(
            "company sales",
            [
                {"table": "mom_level", "column": "netweight_tmt", "retrieval_confidence": 0.30, "rerank_score": 0.4},
                {"table": "industry_performance", "column": "comname", "retrieval_confidence": 0.25, "rerank_score": 0.3},
            ],
            "schema",
            scorer=lambda pairs: [0.1, 0.95],
        )

        self.assertEqual(results[0]["table"], "industry_performance")
        self.assertTrue(results[0]["reranker_applied"])
        self.assertEqual(results[0]["reranker_reason"], "low_confidence")


if __name__ == "__main__":
    unittest.main()
