import json
import unittest
from pathlib import Path
from core.phase6_query_pattern_engine import match_query_pattern

class TrendAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(Path("data/schema_metadata.json").read_text())

    def test_monthly_trend_without_company_uses_mom_level(self):
        match = match_query_pattern(
            "Show the monthly sales trend for Retail SBU in FY 2024-2025",
            schema_metadata=self.schema,
        )
        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "mom_monthly_trend_v1")
        self.assertIn("FROM mom_level", match["sql"])
        self.assertIn("ORDER BY yearmonth", match["sql"])
        self.assertEqual(match["slots"]["sbu"]["value"], "Retail")
        self.assertEqual(match["slots"]["fiscal_year"]["value"], "2024-2025")

    def test_monthly_trend_with_company_uses_industry_performance(self):
        match = match_query_pattern(
            "Give the monthly trend of HPCL sales in FY 2024-2025",
            schema_metadata=self.schema,
        )
        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "industry_monthly_trend_v1")
        self.assertIn("FROM industry_performance", match["sql"])
        self.assertIn("CASE month_name", match["sql"])
        self.assertIn("WHEN 'APR' THEN 1", match["sql"])
        self.assertEqual(match["slots"]["company"]["value"], "HPCL")
        self.assertEqual(match["slots"]["fiscal_year"]["value"], "2024-2025")

    def test_quarterly_trend_without_company_uses_mom_level(self):
        match = match_query_pattern(
            "Show quarterly actual sales trend for LPG in FY 2024-2025",
            schema_metadata=self.schema,
        )
        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "mom_quarterly_trend_v1")
        self.assertIn("FROM mom_level", match["sql"])
        self.assertIn("CASE WHEN month_name IN ('APR', 'MAY', 'JUN') THEN 'Q1'", match["sql"])
        self.assertIn("GROUP BY fiscal_quarter", match["sql"])
        self.assertEqual(match["slots"]["sbu"]["value"], "LPG")
        self.assertEqual(match["slots"]["fiscal_year"]["value"], "2024-2025")

    def test_quarterly_trend_with_company_uses_industry_performance(self):
        match = match_query_pattern(
            "Quarterly sales trend for BPCL in FY 2024-2025",
            schema_metadata=self.schema,
        )
        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "industry_quarterly_trend_v1")
        self.assertIn("FROM industry_performance", match["sql"])
        self.assertIn("CASE WHEN month_name IN ('APR', 'MAY', 'JUN') THEN 'Q1'", match["sql"])
        self.assertEqual(match["slots"]["company"]["value"], "BPCL")
        self.assertEqual(match["slots"]["fiscal_year"]["value"], "2024-2025")

    def test_yearly_trend_without_company(self):
        match = match_query_pattern(
            "Show yearly trend of LPG sales",
            schema_metadata=self.schema,
        )
        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "mom_yearly_trend_v1")
        self.assertIn("FROM mom_level", match["sql"])
        self.assertIn("GROUP BY fiscal_year", match["sql"])
        self.assertEqual(match["slots"]["sbu"]["value"], "LPG")

    def test_yearly_trend_with_company(self):
        match = match_query_pattern(
            "Yearly trend of IOCL actual sales",
            schema_metadata=self.schema,
        )
        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "industry_yearly_trend_v1")
        self.assertIn("FROM industry_performance", match["sql"])
        self.assertEqual(match["slots"]["company"]["value"], "IOCL")

    def test_highest_month_with_company(self):
        match = match_query_pattern(
            "Find the highest month for HPCL sales in FY 2024-2025",
            schema_metadata=self.schema,
        )
        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "industry_highest_lowest_month_v1")
        self.assertIn("ORDER BY actual_sales_tmt DESC", match["sql"])
        self.assertIn("LIMIT 1", match["sql"])
        self.assertEqual(match["slots"]["company"]["value"], "HPCL")
        self.assertEqual(match["slots"]["fiscal_year"]["value"], "2024-2025")

    def test_lowest_month_without_company(self):
        match = match_query_pattern(
            "Find the lowest month for LPG sales in FY 2024-2025",
            schema_metadata=self.schema,
        )
        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "mom_highest_lowest_month_v1")
        self.assertIn("ORDER BY actual_sales_tmt ASC", match["sql"])
        self.assertIn("LIMIT 1", match["sql"])
        self.assertEqual(match["slots"]["sbu"]["value"], "LPG")
        self.assertEqual(match["slots"]["fiscal_year"]["value"], "2024-2025")

    def test_mom_growth_without_company(self):
        match = match_query_pattern(
            "Calculate month over month growth for Retail sales in FY 2024-2025",
            schema_metadata=self.schema,
        )
        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "mom_mom_growth_v1")
        self.assertIn("WITH monthly_sales AS", match["sql"])
        self.assertIn("LAG(sales_tmt) OVER", match["sql"])
        self.assertIn("mom_growth_percentage", match["sql"])
        self.assertEqual(match["slots"]["sbu"]["value"], "Retail")
        self.assertEqual(match["slots"]["fiscal_year"]["value"], "2024-2025")

    def test_mom_growth_with_company(self):
        match = match_query_pattern(
            "Month over month growth of HPCL sales in FY 2024-2025",
            schema_metadata=self.schema,
        )
        self.assertTrue(match["matched"])
        self.assertEqual(match["template_id"], "industry_mom_growth_v1")
        self.assertIn("WITH monthly_sales AS", match["sql"])
        self.assertIn("LAG(sales_tmt) OVER", match["sql"])
        self.assertIn("month_idx", match["sql"])
        self.assertEqual(match["slots"]["company"]["value"], "HPCL")
        self.assertEqual(match["slots"]["fiscal_year"]["value"], "2024-2025")

if __name__ == "__main__":
    unittest.main()
