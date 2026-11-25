import asyncio
import json
import unittest
from pathlib import Path

from core import phase6_orchestrator
from core.phase6_query_pattern_engine import (
    cluster_similar_examples,
    create_query_pattern_cache_tables,
    execution_cache_fingerprint,
    match_query_pattern,
    match_query_pattern_async,
    normalize_sql_for_fingerprint,
    sql_fingerprint,
)


class QueryPatternEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(Path("data/schema_metadata.json").read_text())

    def test_market_share_template_extracts_slots_and_renders_postgres_sql(self):
        match = match_query_pattern(
            "Which company has the lowest market share in fiscal year 2024-2025?",
            schema_metadata=self.schema,
        )

        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "industry_market_share_rank_v1")
        self.assertEqual(match["slots"]["fiscal_year"]["value"], "2024-2025")
        self.assertIn("WITH company_sales AS", match["sql"])
        self.assertIn("CROSS JOIN total_market", match["sql"])
        self.assertIn("ORDER BY market_share_percentage ASC", match["sql"])
        self.assertIn("%s", match["parameterized_sql"])
        self.assertEqual(match["params"], ["2024-2025", "2024-2025"])

    def test_named_company_market_share_keeps_total_market_denominator_unfiltered(self):
        match = match_query_pattern(
            "What is the market share percentage of HPCL in the fiscal year 2024-2025",
            schema_metadata=self.schema,
        )

        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "industry_market_share_rank_v1")
        self.assertEqual(match["sql"].count("industry_performance.comname = 'HPCL'"), 1)
        self.assertEqual(match["params"], ["2024-2025", "HPCL", "2024-2025"])

    def test_named_company_lowest_month_market_share_uses_monthly_denominator(self):
        match = match_query_pattern(
            "In which month did HMEL have the lowest market share in FY 2024-2025?",
            schema_metadata=self.schema,
        )

        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "industry_market_share_rank_v1")
        self.assertIn("SELECT industry_performance.month_name", match["sql"])
        self.assertEqual(match["sql"].count("GROUP BY industry_performance.month_name"), 2)
        self.assertIn(
            "INNER JOIN total_market ON company_sales.month_name IS NOT DISTINCT FROM total_market.month_name",
            match["sql"],
        )
        self.assertEqual(match["sql"].count("industry_performance.comname = 'HMEL'"), 1)
        self.assertEqual(match["params"], ["2024-2025", "HMEL", "2024-2025"])

    def test_zone_market_contribution_for_ly_uses_global_denominator(self):
        match = match_query_pattern(
            "Top 10 Zones in the market contribution for the LY[for all SBU's]",
            schema_metadata=self.schema,
        )

        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "industry_market_share_rank_v1")
        self.assertIn("SELECT industry_performance.zone_name", match["sql"])
        self.assertEqual(match["sql"].count("GROUP BY industry_performance.zone_name"), 1)
        self.assertIn("FROM company_sales\nCROSS JOIN total_market", match["sql"])
        self.assertNotIn("sbu_name", match["sql"])
        self.assertEqual(match["params"], ["2024-2025", "2024-2025"])
        self.assertIn("LIMIT 10", match["sql"])

    def test_actual_sales_template_extracts_dynamic_slots(self):
        match = match_query_pattern(
            "What is actual sales in TMT for Retail HSD from Dec 1 2025 to Dec 15 2025?",
            schema_metadata=self.schema,
        )

        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "mom_actual_sales_sum_v1")
        self.assertEqual(match["slots"]["sbu"]["value"], "Retail")
        self.assertEqual(match["slots"]["product"]["value"], "HSD")
        self.assertEqual(match["slots"]["date_range"]["value"], {"start": "2025-12-01", "end": "2025-12-15"})
        self.assertIn("mom_level.sbu_name NOT IN", match["sql"])
        self.assertIn("mom_level.day_id BETWEEN", match["sql"])

    def test_named_company_sales_uses_industry_template_and_company_filter(self):
        match = match_query_pattern(
            "What is the total sales for HPCL in the Retail SBU for the fiscal year 2024-2025 in TMT",
            schema_metadata=self.schema,
        )

        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "industry_actual_sales_sum_v1")
        self.assertIn("FROM industry_performance", match["sql"])
        self.assertIn("industry_performance.comname = 'HPCL'", match["sql"])
        self.assertIn("industry_performance.sbu_name = 'RETAIL'", match["sql"])
        self.assertIn("industry_performance.fiscal_year = '2024-2025'", match["sql"])

    def test_sma_and_oil_use_industry_company_template(self):
        for company in ["SMA", "OIL"]:
            with self.subTest(company=company):
                match = match_query_pattern(
                    f"What is the Actual Cumulative Volume for {company} in the fiscal year 2024-2025",
                    schema_metadata=self.schema,
                )

                self.assertTrue(match["matched"])
                self.assertEqual(match["template_id"], "industry_actual_sales_sum_v1")
                self.assertIn("FROM industry_performance", match["sql"])
                self.assertIn(f"industry_performance.comname = '{company}'", match["sql"])
                self.assertIn("industry_performance.fiscal_year = '2024-2025'", match["sql"])

    def test_current_financial_year_company_sales_pattern_keeps_configured_fy(self):
        match = match_query_pattern(
            "What is the total sales for I&C in IOCL company for the current financial year",
            schema_metadata=self.schema,
        )

        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "industry_actual_sales_sum_v1")
        self.assertIn("industry_performance.comname = 'IOCL'", match["sql"])
        self.assertIn("industry_performance.sbu_name = 'I&C'", match["sql"])
        self.assertIn("industry_performance.fiscal_year = '2025-2026'", match["sql"])

    def test_sales_growth_comparison_uses_comparison_template(self):
        match = match_query_pattern(
            "What is the sales difference and sales growth percentage for BPCL in the LPG SBU between FY 2023-2024 and FY 2024-2025",
            schema_metadata=self.schema,
        )

        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "industry_sales_growth_comparison_v1")
        self.assertIn("AS sales_difference_tmt", match["sql"])
        self.assertIn("AS sales_growth_percentage", match["sql"])
        self.assertIn("industry_performance.comname = 'BPCL'", match["sql"])
        self.assertIn("industry_performance.sbu_name = 'LPG'", match["sql"])
        self.assertIn("industry_performance.fiscal_year IN ('2023-2024', '2024-2025')", match["sql"])

    def test_gail_lubes_growth_comparison_keeps_sbu_filter(self):
        match = match_query_pattern(
            "What is the sales difference and sales growth percentage for GAIL in the Lubes SBU between the fiscal years 2023-2024 and 2024-2025?",
            schema_metadata=self.schema,
        )

        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "industry_sales_growth_comparison_v1")
        self.assertIn("industry_performance.comname = 'GAIL'", match["sql"])
        self.assertIn("industry_performance.sbu_name = 'LUBES'", match["sql"])
        self.assertIn("industry_performance.fiscal_year IN ('2023-2024', '2024-2025')", match["sql"])

    def test_lubes_south_zone_reverse_year_growth_comparison_uses_verified_pattern(self):
        match = match_query_pattern(
            "Compare Lubes sales in South Zone for FY 2024-2025 against FY 2023-2024 and calculate growth percentage.",
            schema_metadata=self.schema,
        )

        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "industry_sales_growth_comparison_v1")
        self.assertIn("industry_performance.sbu_name = 'LUBES'", match["sql"])
        self.assertIn("industry_performance.zone_name = 'SZ'", match["sql"])
        self.assertIn("industry_performance.fiscal_year IN ('2023-2024', '2024-2025')", match["sql"])
        self.assertIn("AS previous_sales_tmt", match["sql"])
        self.assertIn("AS current_sales_tmt", match["sql"])
        self.assertIn("COALESCE(SUM(CASE", match["sql"])
        self.assertEqual(match["slots"]["fiscal_year_start"]["value"], "2023-2024")
        self.assertEqual(match["slots"]["fiscal_year_end"]["value"], "2024-2025")

    def test_product_growth_threshold_uses_mom_grouped_comparison_pattern(self):
        match = match_query_pattern(
            "Show products whose FY 2024-2025 sales exceeded FY 2023-2024 by more than 20%",
            schema_metadata=self.schema,
        )

        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "mom_product_growth_threshold_v1")
        self.assertIn("FROM mom_level", match["sql"])
        self.assertIn("GROUP BY mom_level.productname", match["sql"])
        self.assertIn("HAVING", match["sql"])
        self.assertIn("> 20.0", match["sql"])
        self.assertIn("AS sales_growth_percentage", match["sql"])
        self.assertEqual(match["parameterized_sql"].count("%s"), len(match["params"]))

    def test_target_versus_actual_variance_by_region_and_month_uses_verified_pattern(self):
        match = match_query_pattern(
            "Compare average target versus actual sales by region for each month and show the variance percentage.",
            schema_metadata=self.schema,
        )

        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "m60_target_actual_variance_v1")
        self.assertIn("m60_level.region_name", match["sql"])
        self.assertIn("m60_level.month_name", match["sql"])
        self.assertIn("AS variance_percentage", match["sql"])
        self.assertIn("AVG(NULLIF(m60_level.target_qty_tmt", match["sql"])
        self.assertIn("'NaN'::numeric", match["sql"])
        self.assertIn("GROUP BY m60_level.region_name, m60_level.month_name", match["sql"])

    def test_sql_fingerprint_ignores_literals_for_clustering(self):
        sql_a = "SELECT * FROM industry_performance WHERE fiscal_year = '2024-2025';"
        sql_b = "SELECT * FROM industry_performance WHERE fiscal_year = '2025-2026';"

        self.assertEqual(sql_fingerprint(sql_a), sql_fingerprint(sql_b))
        self.assertNotEqual(
            sql_fingerprint(sql_a, include_literals=True),
            sql_fingerprint(sql_b, include_literals=True),
        )
        self.assertIn("?", normalize_sql_for_fingerprint(sql_a))

    def test_similarity_clustering_groups_equivalent_sql_shapes(self):
        clusters = cluster_similar_examples([
            {
                "id": 1,
                "question": "market share 2024",
                "sql": "SELECT * FROM industry_performance WHERE fiscal_year = '2024-2025';",
                "similarity": 0.99,
                "complexity": "MEDIUM",
            },
            {
                "id": 2,
                "question": "market share 2025",
                "sql": "SELECT * FROM industry_performance WHERE fiscal_year = '2025-2026';",
                "similarity": 0.97,
                "complexity": "MEDIUM",
            },
        ])

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["size"], 2)
        self.assertEqual(clusters[0]["best_similarity"], 0.99)

    def test_execution_fingerprint_includes_parameters(self):
        sql = "SELECT * FROM industry_performance WHERE fiscal_year = %s;"

        self.assertNotEqual(
            execution_cache_fingerprint(sql, ["2024-2025"]),
            execution_cache_fingerprint(sql, ["2025-2026"]),
        )

    def test_cache_schema_creation_is_migration_safe(self):
        class FakeCursor:
            def __init__(self):
                self.statements = []

            def execute(self, sql):
                self.statements.append(sql)

        cur = FakeCursor()
        create_query_pattern_cache_tables(cur)
        joined = "\n".join(cur.statements)

        self.assertIn("CREATE TABLE IF NOT EXISTS nlpsql_query_pattern_cache", joined)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_nlpsql_pattern_cache_pattern", joined)
        self.assertIn("verification_status", joined)

    def test_async_pattern_match_wrapper(self):
        match = asyncio.run(
            match_query_pattern_async(
                "Get the total target quantity in KL for I&C in North zone",
                schema_metadata=self.schema,
            )
        )

        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "m60_target_quantity_sum_v1")

    def test_orchestrator_uses_pattern_fast_path_without_llm_generation(self):
        originals = {
            "load_schema": phase6_orchestrator.load_schema,
            "get_local_embedder": phase6_orchestrator.get_local_embedder,
            "retrieve_similar_examples": phase6_orchestrator.retrieve_similar_examples,
            "get_verified_sql": phase6_orchestrator.get_verified_sql,
            "store_candidate": phase6_orchestrator.store_candidate,
            "get_cached_pattern_result": phase6_orchestrator.get_cached_pattern_result,
            "store_pattern_result": phase6_orchestrator.store_pattern_result,
            "validate_and_execute": phase6_orchestrator.validate_and_execute,
            "retrieve_similar_schema": phase6_orchestrator.retrieve_similar_schema,
            "generate_sql": phase6_orchestrator.generate_sql,
            "log_query": phase6_orchestrator.log_query,
        }

        import core.logical_planner
        original_classify = core.logical_planner.DeterministicIntentClassifier.classify
        original_plan = core.logical_planner.LogicalQueryPlanner.plan

        try:
            core.logical_planner.DeterministicIntentClassifier.classify = lambda self, query: {"intent": "MARKET_SHARE", "confidence": 0.80}
            core.logical_planner.LogicalQueryPlanner.plan = lambda self, query: None

            phase6_orchestrator.load_schema = lambda schema_path=phase6_orchestrator.SCHEMA_PATH: self.schema
            phase6_orchestrator.get_verified_sql = lambda *args, **kwargs: None
            phase6_orchestrator.store_candidate = lambda *args, **kwargs: True
            phase6_orchestrator.get_local_embedder = lambda: None
            phase6_orchestrator.retrieve_similar_examples = lambda *args, **kwargs: [
                {
                    "id": 1,
                    "question": "Which company has the lowest market share in fiscal year 2024-2025?",
                    "sql": "SELECT 1;",
                    "complexity": "HARD",
                    "similarity": 0.99,
                }
            ]
            phase6_orchestrator.get_cached_pattern_result = lambda *args, **kwargs: None
            phase6_orchestrator.store_pattern_result = lambda *args, **kwargs: True
            phase6_orchestrator.retrieve_similar_schema = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("Schema retrieval should not run on pattern fast-path")
            )
            phase6_orchestrator.generate_sql = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("LLM SQL generation should not run on pattern fast-path")
            )
            phase6_orchestrator.validate_and_execute = lambda **kwargs: {
                "success": True,
                "sql": kwargs["sql"],
                "columns": ["comname", "market_share_percentage"],
                "rows": [["HPCL", 10.5]],
                "row_count": 1,
                "execution_time_ms": 2,
                "warnings": [],
                "error": None,
                "healed": False,
                "validation_reports": [],
            }
            phase6_orchestrator.log_query = lambda *args, **kwargs: 321

            result = phase6_orchestrator.run_pipeline(
                "Which company has the lowest market share in fiscal year 2024-2025?",
                conn_str="unused",
                model="test-model",
            )
        finally:
            core.logical_planner.DeterministicIntentClassifier.classify = original_classify
            core.logical_planner.LogicalQueryPlanner.plan = original_plan
            for name, value in originals.items():
                setattr(phase6_orchestrator, name, value)

        self.assertTrue(result["success"])
        self.assertEqual(result["generation_mode"], "query_pattern_template")
        self.assertTrue(result["query_pattern"]["matched"])
        self.assertTrue(result["query_pattern"]["cache_stored"])
        self.assertEqual(result["schema_context_used"], 0)


if __name__ == "__main__":
    unittest.main()
