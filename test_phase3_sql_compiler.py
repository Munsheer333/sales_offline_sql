import asyncio
import json
import unittest
from pathlib import Path

from core.phase3_query_planner import generate_logical_plan
from core.phase3_sql_compiler import (
    SQLCompilerError,
    compile_logical_plan,
    compile_logical_plan_async,
    generate_parameterized_postgresql_sql,
)


class PostgreSQLCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(Path("data/schema_metadata.json").read_text())

    def test_compiles_single_table_sales_plan_with_parameters(self):
        plan = generate_logical_plan(
            "What is the actual sales in TMT for Retail HSD from Dec 1 2025 to Dec 15 2025?",
            schema_metadata=self.schema,
        )

        compiled = compile_logical_plan(plan, self.schema)

        self.assertTrue(compiled.sql.startswith("SELECT "))
        self.assertIn("FROM mom_level", compiled.sql)
        self.assertIn("productname = 'HSD'", compiled.sql)
        self.assertIn("day_id BETWEEN '2025-12-01' AND '2025-12-15'", compiled.sql)
        self.assertIn("%s", compiled.parameterized_sql)
        self.assertIn("HSD", compiled.params)

    def test_compiles_market_share_with_ctes_and_cross_join(self):
        plan = generate_logical_plan(
            "Which company has the lowest market share in fiscal year 2024-2025?",
            schema_metadata=self.schema,
        )

        compiled = compile_logical_plan(plan, self.schema)

        self.assertIn("WITH company_sales AS", compiled.sql)
        self.assertIn("CROSS JOIN total_market", compiled.sql)
        self.assertIn("GROUP BY industry_performance.comname", compiled.sql)
        self.assertIn("ORDER BY market_share_percentage ASC", compiled.sql)
        self.assertIn("LIMIT 1", compiled.sql)

    def test_compiles_named_company_market_share_without_filtering_total_market(self):
        plan = generate_logical_plan(
            "What is the market share percentage of HPCL in the fiscal year 2024-2025",
            schema_metadata=self.schema,
        )

        compiled = compile_logical_plan(plan, self.schema)

        self.assertEqual(compiled.sql.count("industry_performance.comname = 'HPCL'"), 1)
        self.assertEqual(compiled.params, ("O", "HPCL", "2024-2025", "O", "2024-2025"))

    def test_compiles_named_company_lowest_month_market_share(self):
        plan = generate_logical_plan(
            "In which month did HMEL have the lowest market share in FY 2024-2025?",
            schema_metadata=self.schema,
        )

        compiled = compile_logical_plan(plan, self.schema)

        self.assertIn("SELECT industry_performance.month_name", compiled.sql)
        self.assertEqual(compiled.sql.count("GROUP BY industry_performance.month_name"), 2)
        self.assertIn(
            "INNER JOIN total_market ON company_sales.month_name IS NOT DISTINCT FROM total_market.month_name",
            compiled.sql,
        )
        self.assertEqual(compiled.sql.count("industry_performance.comname = 'HMEL'"), 1)

    def test_compiles_named_company_sales_without_dropping_company_constraint(self):
        plan = generate_logical_plan(
            "What is the total sales for HPCL in the Retail SBU for the fiscal year 2024-2025 in TMT",
            schema_metadata=self.schema,
        )

        compiled = compile_logical_plan(plan, self.schema)

        self.assertIn("FROM industry_performance", compiled.sql)
        self.assertIn("comname = 'HPCL'", compiled.sql)
        self.assertIn("sbu_name = 'RETAIL'", compiled.sql)
        self.assertIn("fiscal_year = '2024-2025'", compiled.sql)

    def test_compiles_top_regions_with_group_by_order_and_limit(self):
        plan = generate_logical_plan(
            "What are the top 10 regions in terms of total industry sales in 2024-2025?",
            schema_metadata=self.schema,
        )

        compiled = compile_logical_plan(plan, self.schema)

        self.assertIn("SELECT industry_performance.region_name", compiled.sql)
        self.assertIn("GROUP BY industry_performance.region_name", compiled.sql)
        self.assertIn("ORDER BY sum_actual_sales_tmt DESC", compiled.sql)
        self.assertIn("LIMIT 10", compiled.sql)

    def test_compiles_zone_market_contribution_with_global_denominator(self):
        plan = generate_logical_plan(
            "Top 10 Zones in the market contribution for the LY[for all SBU's]",
            schema_metadata=self.schema,
        )

        compiled = compile_logical_plan(plan, self.schema)

        self.assertIn("SELECT industry_performance.zone_name", compiled.sql)
        self.assertEqual(compiled.sql.count("GROUP BY industry_performance.zone_name"), 1)
        self.assertIn("FROM company_sales CROSS JOIN total_market", compiled.sql)
        self.assertIn("industry_performance.fiscal_year = '2024-2025'", compiled.sql)
        self.assertIn("LIMIT 10", compiled.sql)

    def test_rejects_top_regions_plan_when_grouping_is_lost(self):
        plan = generate_logical_plan(
            "What are the top 10 regions in terms of total industry sales in 2024-2025?",
            schema_metadata=self.schema,
        )
        plan["group_by"] = []

        with self.assertRaisesRegex(SQLCompilerError, "dropped ranking dimension"):
            compile_logical_plan(plan, self.schema)

    def test_rejects_named_company_plan_when_company_filter_is_lost(self):
        plan = generate_logical_plan(
            "What is the total sales for HPCL in Retail SBU?",
            schema_metadata=self.schema,
        )
        plan["filters"] = [
            item for item in plan["filters"]
            if item.get("source") != "company_name"
        ]

        with self.assertRaisesRegex(SQLCompilerError, "dropped explicit company constraint"):
            compile_logical_plan(plan, self.schema)

    def test_compiles_current_financial_year_company_sales_with_configured_fy(self):
        plan = generate_logical_plan(
            "What is the total sales for I&C in IOCL company for the current financial year",
            schema_metadata=self.schema,
        )

        compiled = compile_logical_plan(plan, self.schema)

        self.assertIn("comname = 'IOCL'", compiled.sql)
        self.assertIn("sbu_name = 'I&C'", compiled.sql)
        self.assertIn("fiscal_year = '2025-2026'", compiled.sql)

    def test_compiles_sales_growth_comparison_successfully(self):
        plan = generate_logical_plan(
            "What is the sales difference and sales growth percentage for BPCL in the LPG SBU between FY 2023-2024 and FY 2024-2025",
            schema_metadata=self.schema,
        )

        compiled = compile_logical_plan(plan, self.schema)
        self.assertIn("WITH sales_totals AS", compiled.sql)
        self.assertIn("sales_difference_tmt", compiled.sql)
        self.assertIn("sales_growth_percentage", compiled.sql)

    def test_rejects_unknown_metric_column(self):
        plan = generate_logical_plan(
            "Rank companies by industry performance sales in AP state.",
            schema_metadata=self.schema,
        )
        plan["metrics"][0]["column"] = "fake_sales_column"

        with self.assertRaises(SQLCompilerError):
            compile_logical_plan(plan, self.schema)

    def test_rejects_hallucinated_source_table(self):
        plan = {
            "is_off_topic": False,
            "source_tables": [{"table": "invented_sales_table"}],
            "metrics": [{"type": "count", "aggregation": "count", "alias": "row_count"}],
            "filters": [],
            "group_by": [],
            "order_by": [],
            "projection": [],
            "limit": None,
        }

        with self.assertRaisesRegex(SQLCompilerError, "Unknown table"):
            compile_logical_plan(plan, self.schema)

    def test_rejects_hallucinated_filter_column(self):
        plan = {
            "is_off_topic": False,
            "source_tables": [{"table": "industry_performance"}],
            "metrics": [
                {
                    "name": "actual_sales",
                    "type": "measure",
                    "column": "netweight_tmt",
                    "aggregation": "sum",
                    "alias": "sum_actual_sales_tmt",
                }
            ],
            "filters": [
                {
                    "table": "industry_performance",
                    "column": "invented_region",
                    "operator": "equal",
                    "value": "North",
                }
            ],
            "group_by": [],
            "order_by": [],
            "projection": [],
            "limit": None,
        }

        with self.assertRaisesRegex(SQLCompilerError, "Unknown column"):
            compile_logical_plan(plan, self.schema)

    def test_generates_parameterized_sql_from_introspected_schema_metadata(self):
        introspected_schema = {
            "sales_fact": {
                "columns": [
                    {"name": "company"},
                    {"name": "sales_tmt"},
                    {"name": "fiscal_year"},
                ],
                "primary_key": "",
                "foreign_keys": [],
            }
        }
        plan = {
            "is_off_topic": False,
            "source_tables": [{"table": "sales_fact"}],
            "metrics": [
                {
                    "name": "actual_sales",
                    "type": "measure",
                    "column": "sales_tmt",
                    "aggregation": "sum",
                    "alias": "sum_sales_tmt",
                }
            ],
            "filters": [
                {
                    "table": "sales_fact",
                    "column": "company",
                    "operator": "equal",
                    "value": "HPCL",
                }
            ],
            "group_by": [],
            "order_by": [],
            "projection": [],
            "limit": 5,
        }

        compiled = generate_parameterized_postgresql_sql(plan, introspected_schema)

        self.assertEqual(
            compiled.parameterized_sql,
            "SELECT ROUND(SUM(sales_fact.sales_tmt)::numeric, 2) AS sum_sales_tmt "
            "FROM sales_fact WHERE sales_fact.company = %s LIMIT %s;",
        )
        self.assertEqual(compiled.params, ("HPCL", 5))
        self.assertEqual(compiled.sql.count("'HPCL'"), 1)

    def test_renders_string_literals_safely_from_parameters(self):
        plan = generate_logical_plan(
            "Get the total target quantity in KL from M60_LEVEL for strategic business unit I&C in North zone.",
            schema_metadata=self.schema,
        )
        plan["filters"][0]["value"] = "I&C's"

        compiled = compile_logical_plan(plan, self.schema)

        self.assertIn("I&C''s", compiled.sql)
        self.assertIn("sbu_name = %s", compiled.parameterized_sql)
        self.assertIn("I&C's", compiled.params)

    def test_rejects_multitable_plan_without_validated_joins(self):
        plan = generate_logical_plan(
            "Rank companies by industry performance sales in AP state.",
            schema_metadata=self.schema,
        )
        plan["source_tables"].append({"table": "mom_level", "reason": "test", "confidence": 0.1})

        with self.assertRaises(SQLCompilerError):
            compile_logical_plan(plan, self.schema)

    def test_async_wrapper_compiles_without_io(self):
        plan = generate_logical_plan(
            "Get the total target quantity in KL from M60_LEVEL for strategic business unit I&C in North zone.",
            schema_metadata=self.schema,
        )

        compiled = asyncio.run(compile_logical_plan_async(plan, self.schema))

        self.assertIn("FROM m60_level", compiled.sql)
        self.assertIn("SUM(m60_level.target_qty_kl)", compiled.sql)


if __name__ == "__main__":
    unittest.main()
