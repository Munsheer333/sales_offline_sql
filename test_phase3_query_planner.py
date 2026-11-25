import json
import unittest
from pathlib import Path

from core import phase3_llm_core
from core.phase3_query_planner import generate_logical_plan


class LogicalQueryPlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(Path("data/schema_metadata.json").read_text())

    def test_actual_sales_extracts_metric_filters_and_date_range(self):
        plan = generate_logical_plan(
            "What is the actual sales in TMT for Retail HSD from Dec 1 2025 to Dec 15 2025?",
            schema_metadata=self.schema,
        )

        self.assertEqual(plan["source_tables"][0]["table"], "mom_level")
        self.assertEqual(plan["metrics"][0]["column"], "netweight_tmt")
        self.assertEqual(plan["aggregations"][0]["function"], "sum")
        self.assertIn(
            {
                "table": "mom_level",
                "column": "productname",
                "operator": "equal",
                "value": "HSD",
                "source": "product",
                "is_business_rule": False,
            },
            plan["filters"],
        )
        self.assertIn(
            {
                "table": "mom_level",
                "column": "day_id",
                "operator": "between",
                "value": {"start": "2025-12-01", "end": "2025-12-15"},
                "source": "date_range",
                "is_business_rule": False,
            },
            plan["filters"],
        )

    def test_market_share_infers_company_grouping(self):
        plan = generate_logical_plan(
            "Which company has the lowest market share in fiscal year 2024-2025?",
            schema_metadata=self.schema,
        )

        self.assertEqual(plan["source_tables"][0]["table"], "industry_performance")
        self.assertEqual(plan["metrics"][0]["type"], "ratio")
        self.assertIn(
            {
                "table": "industry_performance",
                "column": "comname",
                "source": "company",
            },
            plan["group_by"],
        )
        self.assertEqual(plan["order_by"][0]["direction"], "asc")
        self.assertEqual(plan["limit"], 1)

    def test_top_regions_plan_keeps_dimension_grouping(self):
        plan = generate_logical_plan(
            "What are the top 10 regions in terms of total industry sales in 2024-2025?",
            schema_metadata=self.schema,
        )

        self.assertIn(
            {"table": "industry_performance", "column": "region_name", "source": "region"},
            plan["group_by"],
        )
        self.assertEqual(plan["order_by"][0]["direction"], "desc")
        self.assertEqual(plan["limit"], 10)

    def test_zone_market_contribution_for_ly_groups_zone_only(self):
        plan = generate_logical_plan(
            "Top 10 Zones in the market contribution for the LY[for all SBU's]",
            schema_metadata=self.schema,
        )

        self.assertEqual(plan["source_tables"][0]["table"], "industry_performance")
        self.assertEqual(plan["metrics"][0]["type"], "ratio")
        self.assertEqual(
            plan["group_by"],
            [{"table": "industry_performance", "column": "zone_name", "source": "zone"}],
        )
        self.assertIn(
            {
                "table": "industry_performance",
                "column": "fiscal_year",
                "operator": "equal",
                "value": "2024-2025",
                "source": "previous_fiscal_year",
                "is_business_rule": False,
            },
            plan["filters"],
        )
        self.assertEqual(plan["limit"], 10)

    def test_best_and_worst_ranking_support(self):
        # Best -> limit 1, order desc
        plan_best = generate_logical_plan(
            "Which company has the best market share in fiscal year 2024-2025?",
            schema_metadata=self.schema,
        )
        self.assertEqual(plan_best["limit"], 1)
        self.assertEqual(plan_best["order_by"][0]["direction"], "desc")
        self.assertEqual(plan_best["order_by"][0]["field"], "market_share_percentage")
        self.assertEqual(
            plan_best["group_by"],
            [{"table": "industry_performance", "column": "comname", "source": "company"}],
        )

        # Worst -> limit 1, order asc
        plan_worst = generate_logical_plan(
            "Which company has the worst market share in fiscal year 2024-2025?",
            schema_metadata=self.schema,
        )
        self.assertEqual(plan_worst["limit"], 1)
        self.assertEqual(plan_worst["order_by"][0]["direction"], "asc")
        self.assertEqual(plan_worst["order_by"][0]["field"], "market_share_percentage")
        self.assertEqual(
            plan_worst["group_by"],
            [{"table": "industry_performance", "column": "comname", "source": "company"}],
        )

    def test_comparison_queries_support(self):
        # Company vs Company
        plan_comp = generate_logical_plan(
            "Compare HPCL and IOCL sales in West zone for FY 2024-2025.",
            schema_metadata=self.schema,
        )
        self.assertEqual(plan_comp["source_tables"][0]["table"], "industry_performance")
        self.assertIn(
            {
                "table": "industry_performance",
                "column": "comname",
                "operator": "in",
                "value": ["HPCL", "IOCL"],
                "source": "company_name",
                "is_business_rule": False,
            },
            plan_comp["filters"],
        )
        self.assertEqual(
            plan_comp["group_by"],
            [{"table": "industry_performance", "column": "comname", "source": "company"}],
        )

        # SBU vs SBU
        plan_sbu = generate_logical_plan(
            "Compare Retail and Lubes sales in FY 2024-2025.",
            schema_metadata=self.schema,
        )
        self.assertIn(
            {
                "table": "industry_performance",
                "column": "sbu_name",
                "operator": "in",
                "value": ["RETAIL", "LUBES"],
                "source": "sbu",
                "is_business_rule": False,
            },
            plan_sbu["filters"],
        )
        self.assertEqual(
            plan_sbu["group_by"],
            [{"table": "industry_performance", "column": "sbu_name", "source": "sbu"}],
        )

        # Year vs Year
        plan_year = generate_logical_plan(
            "Compare LPG sales in FY 2023-2024 against FY 2024-2025.",
            schema_metadata=self.schema,
        )
        self.assertIn(
            {
                "table": "industry_performance",
                "column": "fiscal_year",
                "operator": "in",
                "value": ["2023-2024", "2024-2025"],
                "source": "fiscal_year_range",
                "is_business_rule": False,
            },
            plan_year["filters"],
        )
        self.assertEqual(
            plan_year["group_by"],
            [{"table": "industry_performance", "column": "fiscal_year", "source": "fiscal year"}],
        )

    def test_named_company_sales_keeps_company_filter_on_industry_table(self):
        plan = generate_logical_plan(
            "What is the total sales for HPCL in the Retail SBU for the fiscal year 2024-2025 in TMT",
            schema_metadata=self.schema,
        )

        self.assertEqual(plan["source_tables"][0]["table"], "industry_performance")
        self.assertIn(
            {
                "table": "industry_performance",
                "column": "comname",
                "operator": "equal",
                "value": "HPCL",
                "source": "company_name",
                "is_business_rule": False,
            },
            plan["filters"],
        )
        self.assertIn(
            {
                "table": "industry_performance",
                "column": "sbu_name",
                "operator": "equal",
                "value": "RETAIL",
                "source": "sbu",
                "is_business_rule": False,
            },
            plan["filters"],
        )

    def test_current_financial_year_company_sales_keeps_configured_fy_filter(self):
        plan = generate_logical_plan(
            "What is the total sales for I&C in IOCL company for the current financial year",
            schema_metadata=self.schema,
        )

        self.assertIn(
            {
                "table": "industry_performance",
                "column": "fiscal_year",
                "operator": "equal",
                "value": "2025-2026",
                "source": "current_fiscal_year",
                "is_business_rule": False,
            },
            plan["filters"],
        )

    def test_sales_growth_comparison_plan_preserves_both_fiscal_years(self):
        plan = generate_logical_plan(
            "What is the sales difference and sales growth percentage for BPCL in the LPG SBU between FY 2023-2024 and FY 2024-2025",
            schema_metadata=self.schema,
        )

        self.assertIn(
            {
                "table": "industry_performance",
                "column": "fiscal_year",
                "operator": "in",
                "value": ["2023-2024", "2024-2025"],
                "source": "fiscal_year_range",
                "is_business_rule": False,
            },
            plan["filters"],
        )

    def test_gail_lubes_growth_comparison_plan_keeps_sbu_filter(self):
        plan = generate_logical_plan(
            "What is the sales difference and sales growth percentage for GAIL in the Lubes SBU between the fiscal years 2023-2024 and 2024-2025?",
            schema_metadata=self.schema,
        )

        self.assertIn(
            {
                "table": "industry_performance",
                "column": "sbu_name",
                "operator": "equal",
                "value": "LUBES",
                "source": "sbu",
                "is_business_rule": False,
            },
            plan["filters"],
        )

    def test_sbu_south_zone_growth_comparison_plan_uses_industry_zone_value(self):
        plan = generate_logical_plan(
            "Compare Lubes sales in South Zone for FY 2024-2025 against FY 2023-2024 and calculate growth percentage.",
            schema_metadata=self.schema,
        )

        self.assertEqual(plan["source_tables"][0]["table"], "industry_performance")
        self.assertIn(
            {
                "table": "industry_performance",
                "column": "zone_name",
                "operator": "equal",
                "value": "SZ",
                "source": "zone",
                "is_business_rule": False,
            },
            plan["filters"],
        )

    def test_target_kl_uses_m60_target_metric(self):
        plan = generate_logical_plan(
            "Get the total target quantity in KL from M60_LEVEL for strategic business unit I&C in North zone.",
            schema_metadata=self.schema,
        )

        self.assertEqual(plan["source_tables"][0]["table"], "m60_level")
        self.assertEqual(plan["metrics"][0]["column"], "target_qty_kl")
        self.assertIn(
            {
                "table": "m60_level",
                "column": "zone_name",
                "operator": "equal",
                "value": "North",
                "source": "zone",
                "is_business_rule": False,
            },
            plan["filters"],
        )

    def test_generate_sql_returns_compiled_mode_without_changing_contract(self):
        original_call = phase3_llm_core.call_ollama
        try:
            def fail_if_llm_called(*args, **kwargs):
                raise AssertionError("LLM should not be called for compiler-supported plans")

            phase3_llm_core.call_ollama = fail_if_llm_called
            result = phase3_llm_core.generate_sql(
                question="Rank companies by industry performance sales in AP state.",
                schema_context=[],
                few_shot_examples=[],
                full_schema_ddl="",
                model="test-model",
                schema_metadata=self.schema,
            )
        finally:
            phase3_llm_core.call_ollama = original_call

        self.assertEqual(result["generation_mode"], "deterministic_compiler")
        self.assertTrue(result["sql"].startswith("SELECT "))
        self.assertEqual(result["model"], "test-model")
        self.assertIsNotNone(result["logical_plan"])
        self.assertIn("parameterized_sql", result)

    def test_generate_sql_falls_back_to_legacy_mode_for_bad_planned_sql(self):
        original_call = phase3_llm_core.call_ollama
        responses = iter(["not sql", "SELECT 2;"])
        try:
            phase3_llm_core.call_ollama = lambda *args, **kwargs: next(responses)
            result = phase3_llm_core.generate_sql(
                question="Show records from the table.",
                schema_context=[],
                few_shot_examples=[],
                full_schema_ddl="",
                model="test-model",
                schema_metadata=self.schema,
            )
        finally:
            phase3_llm_core.call_ollama = original_call

        self.assertEqual(result["generation_mode"], "legacy_fallback")
        self.assertEqual(result["sql"], "SELECT 2;")
        self.assertIsNotNone(result["logical_plan"])
        self.assertIn("planner_error", result)

    def test_generate_sql_uses_planned_llm_fallback_when_compiler_refuses_plan(self):
        original_call = phase3_llm_core.call_ollama
        try:
            phase3_llm_core.call_ollama = lambda *args, **kwargs: "SELECT 3;"
            result = phase3_llm_core.generate_sql(
                question="Show records from the table.",
                schema_context=[],
                few_shot_examples=[],
                full_schema_ddl="",
                model="test-model",
                schema_metadata=self.schema,
            )
        finally:
            phase3_llm_core.call_ollama = original_call

        self.assertEqual(result["generation_mode"], "planned_llm_fallback")
        self.assertEqual(result["sql"], "SELECT 3;")
        self.assertIn("compiler_error", result)


if __name__ == "__main__":
    unittest.main()
