import json
import unittest
from pathlib import Path

from core.business_entity_catalog import analyze_business_entities
from core.phase3_query_planner import generate_logical_plan
from core.phase3_sql_compiler import compile_logical_plan
from core.phase4_semantic_coverage import validate_question_sql_coverage


class SemanticCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(Path("data/schema_metadata.json").read_text())

    def assertMissing(self, report, code):
        self.assertFalse(report["valid"])
        self.assertIn(code, {item["code"] for item in report["errors"]})

    def test_blocks_missing_company_filter(self):
        report = validate_question_sql_coverage(
            "What is the total sales for HPCL in the Retail SBU for the fiscal year 2024-2025 in TMT",
            "SELECT SUM(netweight_tmt) FROM industry_performance "
            "WHERE category != 'O' AND sbu_name = 'RETAIL' AND fiscal_year = '2024-2025';",
            self.schema,
        )
        self.assertMissing(report, "MISSING_FILTER")

    def test_blocks_missing_current_financial_year(self):
        report = validate_question_sql_coverage(
            "What is the total sales for I&C in IOCL company for the current financial year",
            "SELECT SUM(netweight_tmt) FROM industry_performance "
            "WHERE category != 'O' AND comname = 'IOCL' AND sbu_name = 'I&C';",
            self.schema,
        )
        self.assertMissing(report, "MISSING_FILTER")

    def test_blocks_missing_sbu_on_growth_comparison(self):
        report = validate_question_sql_coverage(
            "What is the sales difference and sales growth percentage for GAIL in the Lubes SBU "
            "between the fiscal years 2023-2024 and 2024-2025?",
            "SELECT SUM(CASE WHEN fiscal_year = '2024-2025' THEN netweight_tmt ELSE 0 END) "
            "- SUM(CASE WHEN fiscal_year = '2023-2024' THEN netweight_tmt ELSE 0 END) AS sales_difference_tmt, "
            "1 AS sales_growth_percentage FROM industry_performance "
            "WHERE category != 'O' AND comname = 'GAIL' "
            "AND fiscal_year IN ('2023-2024', '2024-2025');",
            self.schema,
        )
        self.assertMissing(report, "MISSING_FILTER")

    def test_blocks_product_growth_threshold_query_without_growth_filter(self):
        report = validate_question_sql_coverage(
            "Show products whose FY 2024-2025 sales exceeded FY 2023-2024 by more than 20%",
            "SELECT productname, SUM(netweight_tmt) FROM mom_level "
            "WHERE sbu_name != '0' AND fiscal_year IN ('2023-2024', '2024-2025') "
            "GROUP BY productname;",
            self.schema,
        )
        self.assertMissing(report, "MISSING_COMPARISON_OUTPUT")
        self.assertMissing(report, "MISSING_GROWTH_THRESHOLD")

    def test_blocks_company_filter_in_market_share_denominator(self):
        report = validate_question_sql_coverage(
            "What is the market share percentage of HPCL in the fiscal year 2024-2025",
            "WITH company_sales AS ("
            "SELECT comname, SUM(netweight_tmt) AS company_total FROM industry_performance "
            "WHERE category != 'O' AND fiscal_year = '2024-2025' AND comname = 'HPCL' GROUP BY comname"
            "), total_market AS ("
            "SELECT SUM(netweight_tmt) AS market_total FROM industry_performance "
            "WHERE category != 'O' AND fiscal_year = '2024-2025' AND comname = 'HPCL'"
            ") SELECT company_sales.comname, "
            "ROUND((company_sales.company_total * 100.0) / NULLIF(total_market.market_total, 0), 2) "
            "AS market_share_percentage FROM company_sales CROSS JOIN total_market;",
            self.schema,
        )
        self.assertMissing(report, "COMPANY_FILTER_IN_MARKET_DENOMINATOR")

    def test_accepts_total_market_denominator_for_named_company_share(self):
        question = "What is the market share percentage of HPCL in the fiscal year 2024-2025"
        plan = generate_logical_plan(question, schema_metadata=self.schema)
        sql = compile_logical_plan(plan, self.schema).sql
        report = validate_question_sql_coverage(question, sql, self.schema, logical_plan=plan)
        self.assertTrue(report["valid"], report)

    def test_blocks_annual_market_share_for_lowest_month_question(self):
        report = validate_question_sql_coverage(
            "In which month did HMEL have the lowest market share in FY 2024-2025?",
            "WITH company_sales AS ("
            "SELECT SUM(netweight_tmt) AS company_total FROM industry_performance "
            "WHERE category != 'O' AND fiscal_year = '2024-2025' AND comname = 'HMEL'"
            "), total_market AS ("
            "SELECT SUM(netweight_tmt) AS market_total FROM industry_performance "
            "WHERE category != 'O' AND fiscal_year = '2024-2025'"
            ") SELECT ROUND((company_sales.company_total * 100.0) / NULLIF(total_market.market_total, 0), 2) "
            "AS market_share_percentage FROM company_sales CROSS JOIN total_market "
            "ORDER BY market_share_percentage ASC LIMIT 1;",
            self.schema,
        )
        self.assertMissing(report, "MISSING_GROUP_BY")

    def test_blocks_top_regions_without_grouping(self):
        report = validate_question_sql_coverage(
            "What are the top 10 regions in terms of total industry sales in 2024-2025?",
            "SELECT SUM(netweight_tmt) AS sum_actual_sales_tmt FROM industry_performance "
            "WHERE category != 'O' AND fiscal_year = '2024-2025' "
            "ORDER BY sum_actual_sales_tmt DESC LIMIT 10;",
            self.schema,
        )
        self.assertMissing(report, "MISSING_GROUP_BY")

    def test_accepts_compiler_output_for_top_regions(self):
        question = "What are the top 10 regions in terms of total industry sales in 2024-2025?"
        plan = generate_logical_plan(question, schema_metadata=self.schema)
        sql = compile_logical_plan(plan, self.schema).sql
        report = validate_question_sql_coverage(question, sql, self.schema, logical_plan=plan)
        self.assertTrue(report["valid"], report)

    def test_database_catalog_company_is_added_to_plan(self):
        question = "What is the total sales for ACME company in fiscal year 2024-2025?"
        analysis = analyze_business_entities(question, catalog={"company": ["ACME"], "sbu": []})
        plan = generate_logical_plan(
            question,
            schema_metadata=self.schema,
            entity_matches=analysis["matches"],
        )
        sql = compile_logical_plan(plan, self.schema).sql
        self.assertIn("FROM industry_performance", sql)
        self.assertIn("comname = 'ACME'", sql)

    def test_explicit_unknown_company_is_reported(self):
        analysis = analyze_business_entities(
            "What is the total sales for UNKNOWNCO company?",
            catalog={"company": ["HPCL"], "sbu": []},
        )
        self.assertEqual(analysis["unresolved"], [
            {"type": "company", "value": "unknownco", "source": "explicit_label"}
        ])

    def test_curated_sbu_is_not_unresolved_when_database_has_no_rows(self):
        analysis = analyze_business_entities(
            "What is total sales for RIL in the Aviation SBU?",
            catalog={"company": ["RIL"], "sbu": []},
        )
        self.assertNotIn(
            {"type": "sbu", "value": "aviation", "source": "explicit_label"},
            analysis["unresolved"],
        )

    def test_all_sbus_scope_is_not_an_unresolved_entity(self):
        analysis = analyze_business_entities(
            "Top 10 Zones in the market contribution for the LY[for all SBU's]",
            catalog={"company": [], "sbu": ["Retail", "LPG"]},
        )

        self.assertEqual(analysis["unresolved"], [])

    def test_blocks_missing_ratio_calculation(self):
        report = validate_question_sql_coverage(
            "Which states contributed to more than 10% of total company sales?",
            "SELECT comname, statename, SUM(netweight_tmt) FROM industry_performance GROUP BY comname, statename;",
            self.schema,
        )
        self.assertMissing(report, "MISSING_RATIO_CALCULATION")

    def test_blocks_missing_percentage_threshold(self):
        report = validate_question_sql_coverage(
            "Which states contributed to more than 10% of total company sales?",
            "SELECT comname, statename, SUM(netweight_tmt) * 100.0 / 1000 AS market_share_percentage "
            "FROM industry_performance GROUP BY comname, statename;",
            self.schema,
        )
        self.assertMissing(report, "MISSING_PERCENTAGE_THRESHOLD")

    def test_accepts_correct_ratio_and_threshold_query(self):
        report = validate_question_sql_coverage(
            "Which states contributed to more than 10% of total company sales?",
            "WITH state_sales AS ( "
            "  SELECT comname, statename, SUM(netweight_tmt) AS state_sales FROM industry_performance GROUP BY comname, statename "
            "), company_totals AS ( "
            "  SELECT comname, SUM(netweight_tmt) AS company_total_sales FROM industry_performance GROUP BY comname "
            ") "
            "SELECT ss.comname, ss.statename, "
            "  ss.state_sales * 100.0 / NULLIF(ct.company_total_sales, 0) AS contribution_percentage "
            "FROM state_sales ss JOIN company_totals ct ON ss.comname = ct.comname "
            "WHERE (ss.state_sales * 100.0 / NULLIF(ct.company_total_sales, 0)) > 10;",
            self.schema,
        )
        self.assertTrue(report["valid"], report)


if __name__ == "__main__":
    unittest.main()
