import asyncio
import unittest

from core.phase3_relationship_graph import (
    JoinPathPlanner,
    RelationshipGraphManager,
    RelationshipPlanningError,
    enrich_logical_plan_with_joins,
    enrich_logical_plan_with_joins_async,
)
from core.phase3_sql_compiler import compile_logical_plan
from test_schema_fixtures import relationship_schema


class RelationshipGraphTests(unittest.TestCase):
    def test_shortest_path_uses_highest_confidence_join_between_same_tables(self):
        graph = RelationshipGraphManager(relationship_schema())
        planner = JoinPathPlanner(graph)

        path = planner.shortest_path("m60_level", "mom_level")

        self.assertEqual(len(path.edges), 1)
        self.assertEqual(path.edges[0].left_column, "orgsbucd")
        self.assertGreater(path.confidence, 0.9)

    def test_multi_hop_join_path_is_planned_deterministically(self):
        graph = RelationshipGraphManager(relationship_schema())
        planner = JoinPathPlanner(graph)

        join_plan = planner.plan_joins(["m60_level", "industry_performance"])

        self.assertEqual(join_plan["join_strategy"], "shortest_path")
        self.assertEqual(len(join_plan["joins"]), 2)
        self.assertEqual(join_plan["join_paths"][0]["tables"], ["m60_level", "mom_level", "industry_performance"])
        self.assertGreater(join_plan["join_confidence"], 0.8)

    def test_no_path_refuses_cartesian_join(self):
        schema = relationship_schema()
        schema["relationships"] = schema["relationships"][:1]
        graph = RelationshipGraphManager(schema)
        planner = JoinPathPlanner(graph)

        with self.assertRaises(RelationshipPlanningError):
            planner.plan_joins(["m60_level", "industry_performance"])

    def test_logical_plan_enrichment_feeds_compiler_join_builder(self):
        plan = {
            "is_off_topic": False,
            "source_tables": [
                {"table": "m60_level", "reason": "test", "confidence": 0.9},
                {"table": "industry_performance", "reason": "test", "confidence": 0.8},
            ],
            "metrics": [
                {
                    "name": "target_quantity",
                    "type": "measure",
                    "column": "target_qty_tmt",
                    "aggregation": "sum",
                    "alias": "sum_target_quantity",
                }
            ],
            "filters": [],
            "aggregations": [{"function": "sum", "column": "target_qty_tmt", "alias": "sum_target_quantity"}],
            "group_by": [
                {"table": "industry_performance", "column": "comname", "source": "company"},
            ],
            "order_by": [],
            "projection": [],
            "limit": None,
        }

        enriched = enrich_logical_plan_with_joins(plan, relationship_schema())
        compiled = compile_logical_plan(enriched, relationship_schema())

        self.assertEqual(enriched["join_strategy"], "shortest_path")
        self.assertIn("INNER JOIN mom_level ON m60_level.orgsbucd = mom_level.orgsbucd", compiled.sql)
        self.assertIn("INNER JOIN industry_performance ON mom_level.productname = industry_performance.productname", compiled.sql)
        self.assertIn("GROUP BY industry_performance.comname", compiled.sql)

    def test_missing_relationship_metadata_keeps_plan_unjoined(self):
        plan = {
            "is_off_topic": False,
            "source_tables": [
                {"table": "m60_level", "reason": "test", "confidence": 0.9},
                {"table": "industry_performance", "reason": "test", "confidence": 0.8},
            ],
        }
        schema = {"tables": relationship_schema()["tables"], "relationships": []}

        enriched = enrich_logical_plan_with_joins(plan, schema)

        self.assertEqual(enriched["join_strategy"], "unavailable")
        self.assertEqual(enriched["joins"], [])
        self.assertIn("join_planning_error", enriched)

    def test_async_join_enrichment_wrapper(self):
        plan = {
            "is_off_topic": False,
            "source_tables": [
                {"table": "m60_level", "reason": "test", "confidence": 0.9},
                {"table": "mom_level", "reason": "test", "confidence": 0.8},
            ],
        }

        enriched = asyncio.run(enrich_logical_plan_with_joins_async(plan, relationship_schema()))

        self.assertEqual(enriched["join_strategy"], "shortest_path")
        self.assertEqual(len(enriched["joins"]), 1)


if __name__ == "__main__":
    unittest.main()
