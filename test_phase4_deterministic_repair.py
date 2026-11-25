import unittest

from core.phase4_deterministic_repair import repair_sql_deterministically
from test_phase4_ast_validator import validation_schema


class DeterministicRepairTests(unittest.TestCase):
    def test_repairs_invalid_column_with_schema_bounded_match(self):
        result = repair_sql_deterministically(
            "SELECT m60_level.target_qty_tm FROM m60_level;",
            None,
            validation_schema(),
        )

        self.assertTrue(result.changed)
        self.assertIn("m60_level.target_qty_tmt", result.sql)
        self.assertEqual(result.stages[0]["stage"], "invalid_columns")
        self.assertGreater(result.confidence, 0.8)

    def test_repairs_aggregation_misuse_with_group_by(self):
        result = repair_sql_deterministically(
            "SELECT m60_level.sbu_name, SUM(m60_level.target_qty_tmt) AS total_target FROM m60_level;",
            None,
            validation_schema(),
        )

        self.assertTrue(result.changed)
        self.assertIn("GROUP BY m60_level.sbu_name", result.sql)
        self.assertEqual(result.stages[0]["stage"], "aggregation_misuse")

    def test_repairs_alias_conflicts_and_close_order_alias(self):
        result = repair_sql_deterministically(
            "SELECT m60_level.target_qty_tmt AS total, m60_level.engine_id AS total FROM m60_level ORDER BY totl;",
            None,
            validation_schema(),
        )

        self.assertTrue(result.changed)
        self.assertIn("AS total_2", result.sql)
        self.assertIn("ORDER BY total", result.sql)
        self.assertEqual([stage["stage"] for stage in result.stages], ["alias_conflicts", "alias_conflicts"])

    def test_repairs_missing_join_predicate_from_relationship_metadata(self):
        result = repair_sql_deterministically(
            "SELECT m60_level.engine_id, mom_level.productname FROM m60_level JOIN mom_level;",
            None,
            validation_schema(),
        )

        self.assertTrue(result.changed)
        self.assertIn("ON m60_level.orgsbucd = mom_level.orgsbucd", result.sql)
        self.assertEqual(result.stages[0]["stage"], "invalid_joins")

    def test_repairs_wrong_join_predicate_from_relationship_metadata(self):
        result = repair_sql_deterministically(
            "SELECT m60_level.engine_id, mom_level.productname FROM m60_level JOIN mom_level ON m60_level.sbu_name = mom_level.productname;",
            None,
            validation_schema(),
        )

        self.assertTrue(result.changed)
        self.assertIn("ON m60_level.orgsbucd = mom_level.orgsbucd", result.sql)

    def test_repairs_syntax_inconsistencies_without_touching_dangerous_sql(self):
        repaired = repair_sql_deterministically(
            "```sql\nSELECT m60_level.engine_id, FROM m60_level\n```",
            None,
            validation_schema(),
        )
        dangerous = repair_sql_deterministically(
            "DROP TABLE m60_level;",
            None,
            validation_schema(),
        )

        self.assertTrue(repaired.changed)
        self.assertEqual(repaired.sql, "SELECT m60_level.engine_id FROM m60_level;")
        self.assertFalse(dangerous.changed)


if __name__ == "__main__":
    unittest.main()
