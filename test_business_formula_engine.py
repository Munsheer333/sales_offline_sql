import unittest

from core.business_formula_engine import BusinessFormulaError, FORMULA_ENGINE


class BusinessFormulaEngineTests(unittest.TestCase):
    def test_lists_central_business_formulas(self):
        formula_names = {formula.name for formula in FORMULA_ENGINE.list_formulas()}

        self.assertIn("market_share_percentage", formula_names)
        self.assertIn("growth_percentage", formula_names)
        self.assertIn("sales_difference", formula_names)
        self.assertIn("actual_volume", formula_names)

    def test_resolves_formula_aliases(self):
        formula = FORMULA_ENGINE.get("Market Share")

        self.assertEqual(formula.name, "market_share_percentage")
        self.assertEqual(formula.default_alias, "market_share_percentage")

    def test_renders_market_share_formula(self):
        expression = FORMULA_ENGINE.market_share_sql(
            "company_sales.company_total",
            "total_market.market_total",
            alias="market_share_percentage",
        )

        self.assertEqual(
            expression,
            "ROUND((company_sales.company_total * 100.0) / NULLIF(total_market.market_total, 0), 2) "
            "AS market_share_percentage",
        )

    def test_renders_growth_and_difference_formulas(self):
        difference = FORMULA_ENGINE.sales_difference_sql(
            "sales_totals.current_sales",
            "sales_totals.previous_sales",
            alias="sales_difference_tmt",
        )
        growth = FORMULA_ENGINE.growth_percentage_sql(
            "sales_totals.current_sales",
            "sales_totals.previous_sales",
            alias="sales_growth_percentage",
        )

        self.assertIn("sales_totals.current_sales - sales_totals.previous_sales", difference)
        self.assertIn("AS sales_difference_tmt", difference)
        self.assertIn("* 100.0 / sales_totals.previous_sales", growth)
        self.assertIn("AS sales_growth_percentage", growth)

    def test_actual_volume_supports_mt_conversion(self):
        expression = FORMULA_ENGINE.actual_volume_sql(
            "mom_level.netweight_tmt",
            unit="MT",
            alias="sum_actual_sales_mt",
        )

        self.assertEqual(expression, "SUM(mom_level.netweight_tmt) * 1000 AS sum_actual_sales_mt")

    def test_missing_formula_input_is_rejected(self):
        with self.assertRaises(BusinessFormulaError):
            FORMULA_ENGINE.render("market_share_percentage", numerator="company_sales.company_total")


if __name__ == "__main__":
    unittest.main()
