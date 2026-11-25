import asyncio
import json
import unittest
from pathlib import Path

from core import phase6_orchestrator
from core.phase3_context_compressor import (
    compress_generation_context,
    compress_generation_context_async,
)
from core.phase3_llm_core import build_rag_prompt
from test_schema_fixtures import relationship_schema


class ContextCompressorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(Path("data/schema_metadata.json").read_text())

    def test_actual_sales_context_is_pruned_to_relevant_table_and_columns(self):
        raw_context = [
            {"table": "mom_level", "column": "netweight_tmt", "description": "actual sales volume", "similarity": 0.86},
            {"table": "m60_level", "column": "target_qty_tmt", "description": "target quantity", "similarity": 0.42},
            {"table": "industry_performance", "column": "comname", "description": "company name", "similarity": 0.31},
        ]

        result = compress_generation_context(
            "What is actual sales in TMT for Retail HSD from Dec 1 2025 to Dec 15 2025?",
            self.schema,
            raw_context,
        )

        self.assertEqual(result["tables"], ["mom_level"])
        self.assertEqual(
            set(result["columns_by_table"]["mom_level"]),
            {"sbu_name", "productname", "netweight_tmt", "day_id"},
        )
        self.assertNotIn("target_qty_tmt", result["schema_ddl"])
        self.assertLess(result["token_estimate"], result["raw_token_estimate"])
        self.assertTrue(all("description" in item for item in result["schema_context"]))

    def test_market_share_context_keeps_metric_and_business_rule_columns(self):
        result = compress_generation_context(
            "Which company has the lowest market share in fiscal year 2024-2025?",
            self.schema,
            [
                {"table": "industry_performance", "column": "comname", "description": "company name", "similarity": 0.8},
                {"table": "mom_level", "column": "netweight_tmt", "description": "actual sales", "similarity": 0.7},
            ],
        )

        self.assertEqual(result["tables"], ["industry_performance"])
        self.assertEqual(
            set(result["columns_by_table"]["industry_performance"]),
            {"category", "comname", "fiscal_year", "netweight_tmt"},
        )
        self.assertEqual(result["business_metrics"][0]["name"], "market_share_percentage")
        self.assertIn("category TEXT", result["schema_ddl"])

    def test_named_company_sales_context_keeps_company_capable_table(self):
        result = compress_generation_context(
            "What is the total sales for HPCL in the Retail SBU for the fiscal year 2024-2025 in TMT",
            self.schema,
            [
                {"table": "mom_level", "column": "netweight_tmt", "description": "actual sales", "similarity": 0.9},
                {"table": "industry_performance", "column": "comname", "description": "company name", "similarity": 0.7},
            ],
        )

        self.assertEqual(result["tables"], ["industry_performance"])
        self.assertIn("comname", result["columns_by_table"]["industry_performance"])
        self.assertIn("category", result["columns_by_table"]["industry_performance"])
        self.assertIn("netweight_tmt", result["columns_by_table"]["industry_performance"])

    def test_compressed_schema_context_preserves_prompt_builder_contract(self):
        result = compress_generation_context(
            "Get total target quantity in KL for North zone.",
            self.schema,
            [{"table": "m60_level", "column": "target_qty_kl", "description": "target quantity", "similarity": 0.9}],
        )

        prompt = build_rag_prompt(
            "Get total target quantity in KL for North zone.",
            result["schema_context"],
            [],
            result["schema_ddl"],
        )

        self.assertIn("=== SCHEMA CONTEXT ===", prompt)
        self.assertIn("CREATE TABLE m60_level", prompt)
        self.assertIn("target_qty_kl", prompt)

    def test_join_context_uses_relationship_metadata_and_includes_bridge_table(self):
        result = compress_generation_context(
            "Show target quantity by company",
            relationship_schema(),
            [
                {"table": "m60_level", "column": "target_qty_tmt", "description": "target quantity", "similarity": 0.92},
                {"table": "industry_performance", "column": "comname", "description": "company name", "similarity": 0.88},
            ],
        )

        self.assertEqual(len(result["joins"]), 2)
        self.assertIn("mom_level", result["tables"])
        self.assertIn("orgsbucd", result["columns_by_table"]["m60_level"])
        self.assertIn("productname", result["columns_by_table"]["mom_level"])
        self.assertTrue(
            any(item.get("context_type") == "join" for item in result["schema_context"])
        )

    def test_async_wrapper_is_available(self):
        result = asyncio.run(
            compress_generation_context_async(
                "Get total target quantity in KL.",
                self.schema,
                [{"table": "m60_level", "column": "target_qty_kl", "description": "target quantity", "similarity": 0.9}],
            )
        )

        self.assertEqual(result["tables"], ["m60_level"])

    def test_orchestrator_uses_compressed_context_without_changing_generation_flow(self):
        captured = {}
        raw_context = [
            {"table": "mom_level", "column": "netweight_tmt", "description": "actual sales volume", "similarity": 0.86},
            {"table": "m60_level", "column": "target_qty_tmt", "description": "target quantity", "similarity": 0.42},
        ]

        originals = {
            "load_schema": phase6_orchestrator.load_schema,
            "get_local_embedder": phase6_orchestrator.get_local_embedder,
            "retrieve_similar_examples": phase6_orchestrator.retrieve_similar_examples,
            "match_query_pattern": phase6_orchestrator.match_query_pattern,
            "retrieve_similar_schema": phase6_orchestrator.retrieve_similar_schema,
            "classify_complexity": phase6_orchestrator.classify_complexity,
            "generate_sql": phase6_orchestrator.generate_sql,
            "validate_and_execute": phase6_orchestrator.validate_and_execute,
            "log_query": phase6_orchestrator.log_query,
            "_try_early_compiler_fast_path": phase6_orchestrator._try_early_compiler_fast_path,
        }

        import core.logical_planner
        original_classify = core.logical_planner.DeterministicIntentClassifier.classify

        try:
            core.logical_planner.DeterministicIntentClassifier.classify = lambda self, query: {"intent": "TOTAL_SALES", "confidence": 0.0}
            phase6_orchestrator.load_schema = lambda schema_path=phase6_orchestrator.SCHEMA_PATH: self.schema
            phase6_orchestrator.get_local_embedder = lambda: None
            phase6_orchestrator.retrieve_similar_examples = lambda *args, **kwargs: []
            phase6_orchestrator.match_query_pattern = lambda *args, **kwargs: {"matched": False}
            phase6_orchestrator.retrieve_similar_schema = lambda *args, **kwargs: raw_context
            phase6_orchestrator.classify_complexity = lambda *args, **kwargs: "GENERAL"
            phase6_orchestrator._try_early_compiler_fast_path = lambda *args, **kwargs: None

            def fake_generate_sql(**kwargs):
                captured.update(kwargs)
                return {
                    "sql": (
                        "SELECT SUM(mom_level.netweight_tmt) FROM mom_level "
                        "WHERE mom_level.sbu_name = 'Retail' "
                        "AND mom_level.productname = 'HSD' "
                        "AND mom_level.day_id BETWEEN '2025-12-01' AND '2025-12-15';"
                    ),
                    "generation_mode": "test",
                    "logical_plan": None,
                }

            phase6_orchestrator.generate_sql = fake_generate_sql
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
            phase6_orchestrator.log_query = lambda *args, **kwargs: 123

            result = phase6_orchestrator.run_pipeline(
                "What is actual sales in TMT for Retail HSD from Dec 1 2025 to Dec 15 2025?",
                conn_str="unused",
                model="test-model",
            )
        finally:
            core.logical_planner.DeterministicIntentClassifier.classify = original_classify
            for name, value in originals.items():
                setattr(phase6_orchestrator, name, value)

        self.assertTrue(result["success"])
        self.assertTrue(result["context_compression"]["enabled"])
        self.assertEqual(result["raw_schema_context_used"], 2)
        self.assertIn("CREATE TABLE mom_level", captured["full_schema_ddl"])
        self.assertNotIn("CREATE TABLE m60_level", captured["full_schema_ddl"])
        self.assertTrue(all("description" in item for item in captured["schema_context"]))


if __name__ == "__main__":
    unittest.main()
