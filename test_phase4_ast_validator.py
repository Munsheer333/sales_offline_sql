import asyncio
import unittest

from core import phase4_validator
from core.phase4_validator import (
    validate_and_execute,
    validate_and_execute_async,
    validate_generated_sql,
    validate_sql_ast,
    validate_sql_ast_async,
)
from test_schema_fixtures import validation_schema


class ASTValidatorTests(unittest.TestCase):
    def test_disallows_write_ddl_statements(self):
        report = validate_sql_ast("DELETE FROM m60_level WHERE engine_id = 'x';", validation_schema())

        self.assertFalse(report["valid"])
        self.assertEqual(report["errors"][0]["check"], "statement_type")
        self.assertIn("read-only SELECT", report["repair_hints"][0])

    def test_validates_tables_and_columns(self):
        report = validate_sql_ast("SELECT m60_level.fake_column FROM m60_level;", validation_schema())

        self.assertFalse(report["valid"])
        self.assertEqual(report["errors"][0]["check"], "columns")
        self.assertIn("m60_level.fake_column", report["errors"][0]["message"])

    def test_rejects_unknown_table(self):
        report = validate_sql_ast("SELECT invented_sales.id FROM invented_sales;", validation_schema())

        self.assertFalse(report["valid"])
        self.assertEqual(report["errors"][0]["check"], "tables")
        self.assertIn("Unknown table", report["errors"][0]["message"])

    def test_validates_group_by_correctness(self):
        report = validate_sql_ast(
            "SELECT m60_level.sbu_name, SUM(m60_level.target_qty_tmt) AS total_target FROM m60_level;",
            validation_schema(),
        )

        self.assertFalse(report["valid"])
        self.assertEqual(report["errors"][0]["check"], "group_by")
        self.assertIn("GROUP BY", report["repair_hints"][0])

    def test_validates_alias_correctness(self):
        report = validate_sql_ast(
            "SELECT m60_level.target_qty_tmt AS total_target, m60_level.engine_id AS total_target FROM m60_level ORDER BY missing_alias;",
            validation_schema(),
        )

        self.assertFalse(report["valid"])
        messages = " ".join(error["message"] for error in report["errors"])
        self.assertIn("Duplicate SELECT alias", messages)
        self.assertIn("ORDER BY references unknown alias", messages)

    def test_validates_join_predicates_and_relationship_metadata(self):
        valid = validate_sql_ast(
            "SELECT m60_level.engine_id, mom_level.productname FROM m60_level "
            "JOIN mom_level ON m60_level.orgsbucd = mom_level.orgsbucd;",
            validation_schema(),
        )
        invalid = validate_sql_ast(
            "SELECT m60_level.engine_id, mom_level.productname FROM m60_level "
            "JOIN mom_level ON m60_level.sbu_name = mom_level.productname;",
            validation_schema(),
        )

        self.assertTrue(valid["valid"])
        self.assertTrue(valid["joins"][0]["relationship_validated"])
        self.assertFalse(invalid["valid"])
        self.assertEqual(invalid["errors"][0]["check"], "joins")

    def test_validates_join_predicates_from_introspected_foreign_keys(self):
        schema = {
            "orders": {
                "columns": [{"name": "id"}, {"name": "customer_id"}],
                "foreign_keys": [
                    {
                        "column": "customer_id",
                        "references_table": "customers",
                        "references_column": "id",
                    }
                ],
            },
            "customers": {
                "columns": [{"name": "id"}, {"name": "name"}],
                "foreign_keys": [],
            },
        }

        report = validate_sql_ast(
            "SELECT orders.id, customers.name FROM orders "
            "JOIN customers ON orders.customer_id = customers.id;",
            schema,
        )

        self.assertTrue(report["valid"])
        self.assertTrue(report["joins"][0]["relationship_validated"])

    def test_rejects_cartesian_join(self):
        report = validate_sql_ast("SELECT * FROM m60_level JOIN mom_level;", validation_schema())

        self.assertFalse(report["valid"])
        self.assertEqual(report["errors"][0]["check"], "joins")
        self.assertIn("ON/USING", report["errors"][0]["message"])

    def test_rejects_invalid_aggregate_function_and_arguments(self):
        unsupported = validate_sql_ast("SELECT MEDIAN(m60_level.target_qty_tmt) FROM m60_level;", validation_schema())
        invalid_star = validate_sql_ast("SELECT SUM(*) FROM m60_level;", validation_schema())
        nested = validate_sql_ast("SELECT SUM(COUNT(*)) FROM m60_level;", validation_schema())

        self.assertFalse(unsupported["valid"])
        self.assertEqual(unsupported["errors"][0]["check"], "aggregations")
        self.assertFalse(invalid_star["valid"])
        self.assertIn("Only COUNT may use *", invalid_star["errors"][0]["hint"])
        self.assertFalse(nested["valid"])
        self.assertIn("Nested aggregate", nested["errors"][0]["message"])

    def test_validate_generated_sql_rejects_invalid_sql_without_execution(self):
        report = validate_generated_sql("DELETE FROM m60_level WHERE engine_id = 'x';", validation_schema())

        self.assertFalse(report["valid"])
        self.assertEqual(report["errors"][0]["check"], "syntax")

    def test_validate_and_execute_repairs_ast_failures_deterministically_before_explain(self):
        original_explain = phase4_validator.explain_query
        original_execute = phase4_validator.execute_query

        def fake_heal(broken_sql, error_msg, schema_ddl):
            raise AssertionError("LLM repair should not be called for deterministic GROUP BY repair")

        def fake_explain(sql, conn_str):
            return True, "ok", [{"Plan": {"Node Type": "Aggregate"}}]

        def fake_execute(sql, conn_str, timeout_ms=30000):
            return {
                "success": True,
                "columns": ["status", "total_amount"],
                "rows": [["OPEN", 10]],
                "row_count": 1,
                "execution_time_ms": 1,
                "error": None,
            }

        try:
            phase4_validator.explain_query = fake_explain
            phase4_validator.execute_query = fake_execute
            result = validate_and_execute(
                sql="SELECT m60_level.sbu_name, SUM(m60_level.target_qty_tmt) AS total_target FROM m60_level;",
                schema_metadata=validation_schema(),
                conn_str="unused",
                llm_heal_fn=fake_heal,
                schema_ddl="",
                max_retries=2,
            )
        finally:
            phase4_validator.explain_query = original_explain
            phase4_validator.execute_query = original_execute

        self.assertTrue(result["success"])
        self.assertTrue(result["healed"])
        self.assertEqual(len(result["validation_reports"]), 2)
        self.assertTrue(result["deterministic_repaired"])
        self.assertGreater(result["repair_confidence"], 0.8)
        self.assertIn("GROUP BY m60_level.sbu_name", result["sql"])
        self.assertEqual(result["repair_attempts"][0]["stages"][0]["stage"], "aggregation_misuse")

    def test_validate_and_execute_preserves_llm_repair_fallback(self):
        original_explain = phase4_validator.explain_query
        original_execute = phase4_validator.execute_query
        captured = {}

        def fake_heal(broken_sql, error_msg, schema_ddl):
            captured["error_msg"] = error_msg
            return "SELECT m60_level.engine_id FROM m60_level;"

        def fake_explain(sql, conn_str):
            return True, "ok", [{"Plan": {"Node Type": "Seq Scan"}}]

        def fake_execute(sql, conn_str, timeout_ms=30000):
            return {
                "success": True,
                "columns": ["id"],
                "rows": [[1]],
                "row_count": 1,
                "execution_time_ms": 1,
                "error": None,
            }

        try:
            phase4_validator.explain_query = fake_explain
            phase4_validator.execute_query = fake_execute
            result = validate_and_execute(
                sql="SELECT unknownthing FROM m60_level;",
                schema_metadata=validation_schema(),
                conn_str="unused",
                llm_heal_fn=fake_heal,
                schema_ddl="",
                max_retries=2,
            )
        finally:
            phase4_validator.explain_query = original_explain
            phase4_validator.execute_query = original_execute

        self.assertTrue(result["success"])
        self.assertTrue(result["healed"])
        self.assertFalse(result["deterministic_repaired"])
        self.assertEqual(result["repair_attempts"][0]["stages"][0]["stage"], "llm_repair_fallback")
        self.assertIn("AST validation errors", captured["error_msg"])

    def test_validate_and_execute_stops_repeated_repair_candidates(self):
        def same_sql_heal(broken_sql, error_msg, schema_ddl):
            return broken_sql

        result = validate_and_execute(
            sql="SELECT unknownthing FROM m60_level;",
            schema_metadata=validation_schema(),
            conn_str="unused",
            llm_heal_fn=same_sql_heal,
            schema_ddl="",
            max_retries=3,
        )

        self.assertFalse(result["success"])
        self.assertIn("repeated SQL candidate", result["error"])
        self.assertEqual(len(result["validation_reports"]), 1)

    def test_async_validation_entrypoints(self):
        report = asyncio.run(validate_sql_ast_async("SELECT m60_level.engine_id FROM m60_level;", validation_schema()))
        self.assertTrue(report["valid"])

        original_explain = phase4_validator.explain_query
        original_execute = phase4_validator.execute_query
        try:
            phase4_validator.explain_query = lambda sql, conn_str: (True, "ok", [])
            phase4_validator.execute_query = lambda sql, conn_str, timeout_ms=30000: {
                "success": True,
                "columns": ["id"],
                "rows": [[1]],
                "row_count": 1,
                "execution_time_ms": 1,
                "error": None,
            }
            result = asyncio.run(
                validate_and_execute_async("SELECT m60_level.engine_id FROM m60_level;", validation_schema(), "unused")
            )
        finally:
            phase4_validator.explain_query = original_explain
            phase4_validator.execute_query = original_execute

        self.assertTrue(result["success"])

    def test_rejects_missing_dimension_group_by(self):
        # 1. "Show market share of each company" -> Needs GROUP BY comname
        report = validate_sql_ast(
            "SELECT SUM(industry_performance.netweight_tmt) FROM industry_performance;",
            validation_schema(),
            question="Show market share of each company"
        )
        self.assertFalse(report["valid"])
        self.assertEqual(report["errors"][0]["check"], "group_by")
        self.assertIn("Missing mandatory GROUP BY on 'comname'", report["errors"][0]["message"])
        
        # 2. Correct query with GROUP BY -> passes
        report_ok = validate_sql_ast(
            "SELECT comname, SUM(industry_performance.netweight_tmt) FROM industry_performance GROUP BY comname;",
            validation_schema(),
            question="Show market share of each company"
        )
        self.assertTrue(report_ok["valid"])

        # 3. "Sales by product" -> Needs GROUP BY productname
        report_product = validate_sql_ast(
            "SELECT SUM(industry_performance.netweight_tmt) FROM industry_performance;",
            validation_schema(),
            question="Sales by product"
        )
        self.assertFalse(report_product["valid"])
        self.assertEqual(report_product["errors"][0]["check"], "group_by")
        self.assertIn("Missing mandatory GROUP BY on 'productname'", report_product["errors"][0]["message"])

        # 4. Correct product query -> passes
        report_product_ok = validate_sql_ast(
            "SELECT productname, SUM(industry_performance.netweight_tmt) FROM industry_performance GROUP BY productname;",
            validation_schema(),
            question="Sales by product"
        )
        self.assertTrue(report_product_ok["valid"])

        # 5. "Find states whose sales are above average."
        # Should reject because mom_level lacks statename column
        report_state_wrong_table = validate_sql_ast(
            "SELECT SUM(netweight_tmt) FROM mom_level;",
            validation_schema(),
            question="Find states whose sales are above average."
        )
        self.assertFalse(report_state_wrong_table["valid"])
        self.assertEqual(report_state_wrong_table["errors"][0]["check"], "group_by")
        self.assertIn("not present in any of the queried tables", report_state_wrong_table["errors"][0]["message"])

        # Should reject because missing GROUP BY on industry_performance
        report_state_missing_groupby = validate_sql_ast(
            "SELECT SUM(netweight_tmt) FROM industry_performance;",
            validation_schema(),
            question="Find states whose sales are above average."
        )
        self.assertFalse(report_state_missing_groupby["valid"])
        self.assertEqual(report_state_missing_groupby["errors"][0]["check"], "group_by")
        self.assertIn("Missing mandatory GROUP BY on 'statename'", report_state_missing_groupby["errors"][0]["message"])

        # 6. "Find regions where actual achievement is below 80%..."
        # Should reject because missing GROUP BY region_name on m60_level
        report_region_missing_groupby = validate_sql_ast(
            "SELECT SUM(target_qty_tmt) FROM m60_level;",
            validation_schema(),
            question="Find regions where actual achievement is below 80% but target exceeds 1000 TMT."
        )
        self.assertFalse(report_region_missing_groupby["valid"])
        self.assertEqual(report_region_missing_groupby["errors"][0]["check"], "group_by")
        self.assertIn("Missing mandatory GROUP BY on 'region_name'", report_region_missing_groupby["errors"][0]["message"])


if __name__ == "__main__":
    unittest.main()
