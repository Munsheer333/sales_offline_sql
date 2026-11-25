import asyncio
import json
import unittest
from pathlib import Path

from core import phase5_router
from core.phase5_router import (
    classify_complexity,
    classify_complexity_llm,
    parse_time_ranges,
    route_question,
    route_question_async,
)


class HybridRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(Path("data/schema_metadata.json").read_text())

    def test_classify_complexity_is_deterministic_by_default(self):
        original_post = phase5_router.requests.post
        try:
            phase5_router.requests.post = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("LLM classifier should not be called by default")
            )
            complexity = classify_complexity(
                "What is actual sales in TMT for Retail HSD from Dec 1 2025 to Dec 15 2025?"
            )
        finally:
            phase5_router.requests.post = original_post

        self.assertEqual(complexity, "MEDIUM")

    def test_route_question_extracts_metric_filters_and_date_range(self):
        route = route_question(
            "What is actual sales in TMT for Retail HSD from Dec 1 2025 to Dec 15 2025?",
            schema_metadata=self.schema,
        )

        self.assertEqual(route["routing_mode"], "deterministic")
        self.assertFalse(route["llm_used"])
        self.assertEqual(route["intent"], "metric_query")
        self.assertEqual(route["metrics"][0]["name"], "actual_sales")
        self.assertIn({"type": "product", "column_candidates": ["productname", "materialgroupname", "prod1"], "operator": "=", "value": "HSD"}, route["filters"])
        self.assertIn({"type": "sbu", "column_candidates": ["sbu_name"], "operator": "=", "value": "Retail"}, route["filters"])
        self.assertEqual(route["time_ranges"][0]["value"], {"start": "2025-12-01", "end": "2025-12-15"})
        self.assertEqual(route["source_tables"][0]["table"], "mom_level")

    def test_route_question_identifies_market_share_and_fiscal_year(self):
        route = route_question(
            "Which company has the lowest market share in fiscal year 2024-2025?",
            schema_metadata=self.schema,
        )

        self.assertEqual(route["intent"], "comparison_or_ranking")
        self.assertEqual(route["metrics"][0]["name"], "market_share_percentage")
        self.assertEqual(route["aggregations"][0]["function"], "ratio")
        self.assertEqual(route["time_ranges"][0]["value"], "2024-2025")
        self.assertEqual(route["source_tables"][0]["table"], "industry_performance")

    def test_top_regions_routing_keeps_region_dimension(self):
        route = route_question(
            "What are the top 10 regions in terms of total industry sales in 2024-2025?",
            schema_metadata=self.schema,
        )

        self.assertIn(
            {"source": "regions", "column_candidates": ["region_name", "ro"], "confidence": 0.80},
            route["dimensions"],
        )

    def test_named_company_sales_routes_to_company_capable_table(self):
        route = route_question(
            "What is the total sales for HPCL in the Retail SBU for the fiscal year 2024-2025 in TMT",
            schema_metadata=self.schema,
        )

        self.assertEqual(route["source_tables"], [
            {
                "table": "industry_performance",
                "reason": "industry_or_company_keyword",
                "confidence": 0.88,
            },
        ])
        self.assertIn(
            {
                "type": "company",
                "column_candidates": ["comname", "coname", "company_name"],
                "operator": "=",
                "value": "HPCL",
            },
            route["filters"],
        )

    def test_sma_and_oil_route_as_company_filters(self):
        for company in ["SMA", "OIL"]:
            with self.subTest(company=company):
                route = route_question(
                    f"What is the Actual Cumulative Volume for {company} in the fiscal year 2024-2025",
                    schema_metadata=self.schema,
                )

                self.assertEqual(route["source_tables"][0]["table"], "industry_performance")
                self.assertIn(
                    {
                        "type": "company",
                        "column_candidates": ["comname", "coname", "company_name"],
                        "operator": "=",
                        "value": company,
                    },
                    route["filters"],
                )

    def test_time_range_parser_handles_current_fiscal_year_without_hardcoding(self):
        ranges = parse_time_ranges("Show sales for current fiscal year")

        self.assertEqual(ranges[0]["type"], "fiscal_year")
        self.assertFalse(ranges[0]["dynamic"])
        self.assertEqual(ranges[0]["value"], "2025-2026")

    def test_zone_market_contribution_for_ly_routes_as_ratio_without_sbu_grouping(self):
        route = route_question(
            "Top 10 Zones in the market contribution for the LY[for all SBU's]",
            schema_metadata=self.schema,
        )

        self.assertEqual(route["metrics"][0]["name"], "market_share_percentage")
        self.assertEqual(route["time_ranges"][0]["value"], "2024-2025")
        self.assertIn(
            {"source": "zones", "column_candidates": ["zone_name"], "confidence": 0.80},
            route["dimensions"],
        )
        self.assertNotIn("sbu", {item["source"] for item in route["dimensions"]})
        self.assertEqual(route["source_tables"][0]["table"], "industry_performance")

    def test_sbu_zone_growth_comparison_routes_to_industry_table(self):
        route = route_question(
            "Compare Lubes sales in South Zone for FY 2024-2025 against FY 2023-2024 and calculate growth percentage.",
            schema_metadata=self.schema,
        )

        self.assertEqual(route["source_tables"][0]["table"], "industry_performance")
        self.assertNotIn("mom_level", {item["table"] for item in route["source_tables"]})

    def test_current_financial_year_company_sales_keeps_fy_filter(self):
        route = route_question(
            "What is the total sales for I&C in IOCL company for the current financial year",
            schema_metadata=self.schema,
        )

        self.assertIn(
            {
                "type": "fiscal_year",
                "column_candidates": ["fiscal_year", "fiscalyear"],
                "operator": "=",
                "value": "2025-2026",
                "dynamic": False,
            },
            route["time_ranges"],
        )

    def test_sales_growth_comparison_extracts_both_fiscal_years(self):
        route = route_question(
            "What is the sales difference and sales growth percentage for BPCL in the LPG SBU between FY 2023-2024 and FY 2024-2025",
            schema_metadata=self.schema,
        )

        self.assertEqual(route["intent"], "trend_analysis")
        self.assertIn(
            {
                "type": "fiscal_year_range",
                "column_candidates": ["fiscal_year", "fiscalyear"],
                "operator": "in",
                "value": ["2023-2024", "2024-2025"],
                "dynamic": False,
            },
            route["time_ranges"],
        )

    def test_gail_lubes_growth_comparison_keeps_sbu_filter(self):
        route = route_question(
            "What is the sales difference and sales growth percentage for GAIL in the Lubes SBU between the fiscal years 2023-2024 and 2024-2025?",
            schema_metadata=self.schema,
        )

        self.assertIn(
            {
                "type": "sbu",
                "column_candidates": ["sbu_name"],
                "operator": "=",
                "value": "Lubes",
            },
            route["filters"],
        )

    def test_llm_classifier_fallback_is_still_available(self):
        original_post = phase5_router.requests.post

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"response": "HARD"}

        try:
            phase5_router.requests.post = lambda *args, **kwargs: FakeResponse()
            self.assertEqual(classify_complexity_llm("ambiguous analytical request", model="test-model"), "HARD")
        finally:
            phase5_router.requests.post = original_post

    def test_async_router_wrapper(self):
        route = asyncio.run(route_question_async("Get total target quantity in KL.", self.schema))

        self.assertEqual(route["metrics"][0]["name"], "target_quantity")
        self.assertEqual(route["complexity"], "MEDIUM")


if __name__ == "__main__":
    unittest.main()
