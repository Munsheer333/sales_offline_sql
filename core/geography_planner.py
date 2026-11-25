"""
NLPSQL SYSTEM — GEOGRAPHY PLANNER
=================================
Plans and refines hierarchy-aware query plans involving geographic dimensions:
  - Zone     (highest level)
  - State
  - Region
  - District (lowest level)

Enforces hierarchy-aware table routing:
  - If "district" is requested (grouping or filter): target_table MUST be "industry_performance".
  - Refines filter plans when multiple levels are specified simultaneously (e.g., district + state).
"""

from __future__ import annotations

from typing import Dict, List, Any, Optional

from core.semantic_registry import get_registry


class GeographyPlanner:
    """
    Enforces geographic hierarchy rules, table routing, and filter validation.
    """

    def __init__(self):
        self.registry = get_registry()

    def plan_geography(
        self,
        target_table: str,
        group_by: List[str],
        filters: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Validates geographic hierarchy constraints and refines table routing.

        Args:
            target_table: Routed target table.
            group_by: Current group by columns.
            filters: Current planned filters.

        Returns:
            Dict containing refined "target_table", "group_by", and "filters".
        """
        refined_table = target_table
        refined_group_by = list(group_by)

        # ── 1. Hierarchy Columns Mapping ──────────────────────────────────────
        # Map levels to database column names for industry_performance
        dist_col = self.registry.resolve_column("district", "industry_performance") or "distname"
        state_col = self.registry.resolve_column("state", "industry_performance") or "statename"
        region_col = self.registry.resolve_column("region", "industry_performance") or "ro"
        zone_col = self.registry.resolve_column("zone", "industry_performance") or "zone_name"

        # ── 2. Enforce Table Routing Based on Lowest Level (District) ────────
        has_district_group = dist_col in refined_group_by
        has_district_filter = any(f.get("concept") == "district" for f in filters)

        if has_district_group or has_district_filter:
            # District is only available on industry_performance. Overwrite routing.
            refined_table = "industry_performance"

        # ── 3. Validate Geography Filter Combinations ─────────────────────────
        # Check if we have parent-child relations. Since the DB is denormalized,
        # we can apply multiple geographic filters simultaneously.
        # Ensure that no filter contains empty value lists.
        clean_filters = []
        for f in filters:
            if f.get("concept") in ("zone", "state", "region", "district"):
                # Ensure values list is not empty
                if not f.get("values"):
                    continue
            clean_filters.append(f)

        return {
            "target_table": refined_table,
            "group_by": refined_group_by,
            "filters": clean_filters
        }


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_geography_planner() -> GeographyPlanner:
    return GeographyPlanner()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    planner = GeographyPlanner()

    print("=== Geography Planner Tests ===")

    # Test Case 1: District Filter forces industry_performance routing
    print("\nTest Case 1: District Filter Routing")
    res1 = planner.plan_geography(
        target_table="mom_level", # Initially routed to mom_level
        group_by=[],
        filters=[{"concept": "district", "column": "distname", "operator": "=", "values": ["PUNE"], "type": "dimension"}]
    )
    print(f"  Target Table: {res1['target_table']} (Expected: industry_performance)")
    print(f"  Filters:      {res1['filters']}")

    # Test Case 2: Multi-level Geographic filters
    print("\nTest Case 2: Multi-level Geographic Filter Clean")
    res2 = planner.plan_geography(
        target_table="industry_performance",
        group_by=["distname"],
        filters=[
            {"concept": "state", "column": "statename", "operator": "=", "values": ["MAHARASHTRA"], "type": "dimension"},
            {"concept": "zone", "column": "zone_name", "operator": "=", "values": [], "type": "dimension"} # Empty! Should be cleaned
        ]
    )
    print(f"  Target Table: {res2['target_table']}")
    print(f"  Group By:     {res2['group_by']}")
    print(f"  Filters:      {res2['filters']}")
