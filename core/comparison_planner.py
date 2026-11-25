"""
NLPSQL SYSTEM — COMPARISON PLANNER
===================================
Plans SQL select metrics, grouping dimensions, and filtering constraints for
side-by-side comparison queries.

Supports comparison across:
  - Company  (comname)
  - SBU      (sbu_name)
  - Year     (fiscal_year)
  - Region   (ro / region_name)
  - Zone     (zone_name)
  - State    (statename)
  - District (distname)

Comparison queries (e.g., HPCL vs BPCL) are planned by grouping by the target
dimension and filtering by the target comparison values using an IN clause.
"""

from __future__ import annotations

import re
from typing import Dict, List, Any, Optional

from core.semantic_registry import get_registry


class ComparisonPlanner:
    """
    Plans grouping and filtering for side-by-side comparison queries.
    """

    def __init__(self):
        self.registry = get_registry()

    def plan_comparison(
        self,
        question: str,
        target_table: str,
        group_by: List[str],
        filters: List[Dict[str, Any]],
        entities: Dict[str, List[str]]
    ) -> Dict[str, Any]:
        """
        Validates comparison targets and adds grouping dimensions + filtering.

        Args:
            question: Original question.
            target_table: Target SQL table name.
            group_by: Current group by columns.
            filters: Current planned filters.
            entities: Extracted structured entities.

        Returns:
            Dict containing updated filters and group_by lists.
        """
        q_lower = question.lower()
        refined_group_by = list(group_by)
        refined_filters = list(filters)

        # ── 1. Map Concept Columns ────────────────────────────────────────────
        comname_col = self.registry.resolve_column("company", target_table) or "comname"
        sbu_col = self.registry.resolve_column("sbu", target_table) or "sbu_name"
        prod_col = self.registry.resolve_column("product", target_table) or "productname"
        region_col = self.registry.resolve_column("region", target_table) or "ro"
        zone_col = self.registry.resolve_column("zone", target_table) or "zone_name"
        dist_col = self.registry.resolve_column("district", target_table) or "distname"
        state_col = self.registry.resolve_column("state", target_table) or "statename"
        year_col = self.registry.resolve_column("fiscal_year", target_table) or "fiscal_year"
        month_col = self.registry.resolve_column("month", target_table) or "month_name"

        # ── 2. Identify the Target Comparison Dimension ───────────────────────
        comp_dim_col = None
        concept_key = None
        values_to_compare = []

        # Check fiscal years
        if len(entities.get("fiscal_year", [])) >= 2 or len(entities.get("year", [])) >= 2:
            comp_dim_col = year_col
            concept_key = "fiscal_year"
            values_to_compare = entities.get("fiscal_year") or entities.get("year", [])
        # Check products
        elif len(entities.get("product", [])) >= 2:
            comp_dim_col = prod_col
            concept_key = "product"
            values_to_compare = entities["product"]
        # Check companies
        elif len(entities.get("company", [])) >= 2:
            comp_dim_col = comname_col
            concept_key = "company"
            values_to_compare = entities["company"]
        # Check SBUs
        elif len(entities.get("sbu", [])) >= 2:
            comp_dim_col = sbu_col
            concept_key = "sbu"
            values_to_compare = entities["sbu"]
        # Check Months
        elif len(entities.get("month", [])) >= 2:
            comp_dim_col = month_col
            concept_key = "month"
            values_to_compare = entities["month"]
        # Check Regions
        elif len(entities.get("region", [])) >= 2:
            comp_dim_col = region_col
            concept_key = "region"
            values_to_compare = entities["region"]
        # Check States
        elif len(entities.get("state", [])) >= 2:
            comp_dim_col = state_col
            concept_key = "state"
            values_to_compare = entities["state"]
        # Check Zones
        elif len(entities.get("zone", [])) >= 2:
            comp_dim_col = zone_col
            concept_key = "zone"
            values_to_compare = entities["zone"]

        # ── 3. Inject Grouping and Set Up Filters ─────────────────────────────
        if comp_dim_col and concept_key:
            # Grouping by the comparison dimension is MANDATORY
            if comp_dim_col not in refined_group_by:
                refined_group_by.append(comp_dim_col)

            # Ensure filter exists for comparison dimension using IN operator
            # Remove any existing single-value filter for this concept first to avoid conflicts
            refined_filters = [f for f in refined_filters if f.get("concept") != concept_key]
            
            refined_filters.append({
                "concept": concept_key,
                "column": comp_dim_col,
                "operator": "IN",
                "values": values_to_compare,
                "type": "dimension",
                "note": "Automatically planned comparison filter"
            })

        return {
            "group_by": refined_group_by,
            "filters": refined_filters
        }


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_comparison_planner() -> ComparisonPlanner:
    return ComparisonPlanner()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    planner = ComparisonPlanner()

    print("=== Comparison Planner Tests ===")

    # Test Case 1: Company Comparison (HPCL vs BPCL)
    print("\nTest Case 1: Company Comparison (HPCL vs BPCL)")
    res1 = planner.plan_comparison(
        question="Compare HPCL vs BPCL sales",
        target_table="industry_performance",
        group_by=[],
        filters=[],
        entities={"company": ["HPCL", "BPCL"]}
    )
    print(f"  Group By: {res1['group_by']}")
    print(f"  Filters:  {res1['filters']}")

    # Test Case 2: SBU Comparison (LPG vs Lubes)
    print("\nTest Case 2: SBU Comparison (LPG vs Lubes)")
    res2 = planner.plan_comparison(
        question="Compare LPG vs Lubes",
        target_table="industry_performance",
        group_by=[],
        filters=[],
        entities={"sbu": ["LPG", "LUBES"]}
    )
    print(f"  Group By: {res2['group_by']}")
    print(f"  Filters:  {res2['filters']}")

    # Test Case 3: Region Comparison
    print("\nTest Case 3: Region Comparison (Mumbai vs Pune)")
    res3 = planner.plan_comparison(
        question="Region wise comparison of Mumbai and Pune",
        target_table="industry_performance",
        group_by=[],
        filters=[],
        entities={"region": ["MUMBAI", "PUNE"]}
    )
    print(f"  Group By: {res3['group_by']}")
    print(f"  Filters:  {res3['filters']}")
