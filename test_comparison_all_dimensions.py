import unittest
from core.logical_planner import LogicalQueryPlanner
from core.sql_assembler import SQLAssembler


class ComparisonAllDimensionsTest(unittest.TestCase):
    def setUp(self):
        self.planner = LogicalQueryPlanner()
        self.assembler = SQLAssembler()

    def test_compare_fiscal_years(self):
        q = "Compare FY 2023-2024 and FY 2024-2025 sales."
        plan = self.planner.plan(q)
        self.assertEqual(plan["intent"], "COMPARE")
        self.assertEqual(plan["dimension"], "fiscal_year")
        self.assertEqual(plan["comparison"], ["2023-2024", "2024-2025"])
        self.assertNotIn("fiscal_year", plan["filters"])
        
        sql = self.assembler.assemble(plan, question=q)
        self.assertIn("fiscal_year IN ('2023-2024', '2024-2025')", sql)
        self.assertIn("GROUP BY fiscal_year", sql)
        self.assertIn("ORDER BY fiscal_year;", sql)
        self.assertNotIn("productname", sql)
        self.assertNotIn("fiscal_year = '2024-2025'", sql)

    def test_compare_products(self):
        q = "Compare LUBES and TMT sales."
        plan = self.planner.plan(q)
        self.assertEqual(plan["intent"], "COMPARE")
        self.assertEqual(plan["dimension"], "product")
        self.assertIn("LUBES", plan["comparison"])
        self.assertIn("TMT", plan["comparison"])
        self.assertNotIn("product", plan["filters"])
        
        sql = self.assembler.assemble(plan, question=q)
        self.assertIn("productname IN ('LUBES', 'TMT')", sql)
        self.assertIn("GROUP BY productname", sql)
        self.assertIn("ORDER BY total_sales_tmt DESC;", sql)

    def test_compare_companies(self):
        q = "Compare HPCL and IOCL sales."
        plan = self.planner.plan(q)
        self.assertEqual(plan["intent"], "COMPARE")
        self.assertEqual(plan["dimension"], "company")
        self.assertIn("HPCL", plan["comparison"])
        self.assertIn("IOCL", plan["comparison"])
        
        sql = self.assembler.assemble(plan, question=q)
        self.assertIn("FROM industry_performance", sql)
        self.assertIn("comname IN ('HPCL', 'IOCL')", sql)
        self.assertIn("GROUP BY comname", sql)

    def test_compare_sbus(self):
        q = "Compare RETAIL and LPG sales."
        plan = self.planner.plan(q)
        self.assertEqual(plan["intent"], "COMPARE")
        self.assertEqual(plan["dimension"], "sbu")
        self.assertIn("RETAIL", plan["comparison"])
        self.assertIn("LPG", plan["comparison"])
        
        sql = self.assembler.assemble(plan, question=q)
        self.assertIn("sbu_name IN ('RETAIL', 'LPG')", sql)
        self.assertIn("GROUP BY sbu_name", sql)

    def test_compare_states(self):
        q = "Compare Maharashtra and Gujarat sales."
        plan = self.planner.plan(q)
        self.assertEqual(plan["intent"], "COMPARE")
        self.assertEqual(plan["dimension"], "state")
        self.assertEqual([c.upper() for c in plan["comparison"]], ["MAHARASHTRA", "GUJARAT"])
        
        sql = self.assembler.assemble(plan, question=q)
        self.assertIn("statename IN ('MAHARASHTRA', 'GUJARAT')", sql)
        self.assertIn("GROUP BY statename", sql)

    def test_compare_months(self):
        q = "Compare April and May sales."
        plan = self.planner.plan(q)
        self.assertEqual(plan["intent"], "COMPARE")
        self.assertEqual(plan["dimension"], "month")
        
        sql = self.assembler.assemble(plan, question=q)
        self.assertIn("month_name IN ('April', 'May')", sql)
        self.assertIn("GROUP BY month_name", sql)
        self.assertIn("ORDER BY month_name;", sql)

    def test_compare_product_performance_between_two_fiscal_years(self):
        q = "Compare product performance between two fiscal years."
        plan = self.planner.plan(q)
        self.assertEqual(plan["intent"], "COMPARE")
        self.assertEqual(plan["dimension"], "product")
        self.assertEqual(plan["comparison_dimension"], "fiscal_year")
        self.assertEqual(plan["dimensions"], ["product", "fiscal_year"])
        self.assertEqual(plan["comparison"], ["2023-2024", "2024-2025"])
        self.assertNotIn("fiscal_year", plan["filters"])
        
        sql = self.assembler.assemble(plan, question=q)
        self.assertIn("SELECT productname, fiscal_year,", sql)
        self.assertIn("FROM m60_level", sql)
        self.assertIn("WHERE fiscal_year IN ('2023-2024', '2024-2025')", sql)
        self.assertIn("GROUP BY productname, fiscal_year", sql)
        self.assertIn("ORDER BY productname, fiscal_year;", sql)

    def test_compare_company_performance_between_two_fiscal_years(self):
        q = "Compare company performance between two fiscal years."
        plan = self.planner.plan(q)
        self.assertEqual(plan["intent"], "COMPARE")
        self.assertEqual(plan["dimension"], "company")
        self.assertEqual(plan["comparison_dimension"], "fiscal_year")
        self.assertEqual(plan["dimensions"], ["company", "fiscal_year"])
        
        sql = self.assembler.assemble(plan, question=q)
        self.assertIn("SELECT comname, fiscal_year,", sql)
        self.assertIn("FROM industry_performance", sql)
        self.assertIn("WHERE fiscal_year IN ('2023-2024', '2024-2025')", sql)
        self.assertIn("GROUP BY comname, fiscal_year", sql)
        self.assertIn("ORDER BY comname, fiscal_year;", sql)

    def test_compare_product_performance_between_explicit_fiscal_years(self):
        q = "Compare product performance between FY 2023-2024 and FY 2024-2025."
        plan = self.planner.plan(q)
        self.assertEqual(plan["intent"], "COMPARE")
        self.assertEqual(plan["dimension"], "product")
        self.assertEqual(plan["comparison_dimension"], "fiscal_year")
        self.assertEqual(plan["dimensions"], ["product", "fiscal_year"])
        self.assertEqual(plan["comparison"], ["2023-2024", "2024-2025"])
        
        sql = self.assembler.assemble(plan, question=q)
        self.assertIn("SELECT productname, fiscal_year,", sql)
        self.assertIn("FROM m60_level", sql)
        self.assertIn("WHERE fiscal_year IN ('2023-2024', '2024-2025')", sql)
        self.assertIn("GROUP BY productname, fiscal_year", sql)
        self.assertIn("ORDER BY productname, fiscal_year;", sql)


if __name__ == "__main__":
    unittest.main()
