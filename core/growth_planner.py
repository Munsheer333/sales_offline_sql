"""
NLPSQL SYSTEM — GROWTH & COMPARISON PLANNER
===========================================
Plans SQL expressions for Growth % and Sales Difference calculations.

Supports YoY/MoM percentage growth and absolute volume deltas across:
  - Company  (comname)
  - SBU      (sbu_name)
  - Region   (ro / region_name)
  - Zone     (zone_name)
  - District (distname)
  - State    (statename)
"""

from __future__ import annotations

import re
from typing import Dict, List, Any, Optional

from core.semantic_registry import get_registry
from core.formula_registry import get_formula_registry


class GrowthPlanner:
    """
    Plans growth (YoY/MoM) and sales difference delta calculations.
    """

    def __init__(self):
        self.registry = get_registry()
        self.formulas = get_formula_registry()

    def plan_growth_or_difference(
        self,
        question: str,
        intent: str,
        target_table: str,
        group_by: List[str],
        entities: Dict[str, List[str]],
        sales_col: str = "netweight_tmt"
    ) -> Dict[str, Any]:
        """
        Plans exact expressions for growth and sales differences.

        Args:
            question: Original natural language question.
            intent: "growth" or "sales_difference".
            target_table: Resolved target table (e.g. "industry_performance").
            group_by: Group-by columns.
            entities: Extracted dictionary of entities.
            sales_col: Physical sales column.

        Returns:
            Dict containing SQL select expression, alias, and window info.
        """
        q_lower = question.lower()
        
        # ── 1. Map Concept Columns ────────────────────────────────────────────
        comname_col = self.registry.resolve_column("company", target_table) or "comname"
        sbu_col = self.registry.resolve_column("sbu", target_table) or "sbu_name"
        region_col = self.registry.resolve_column("region", target_table) or "ro"
        zone_col = self.registry.resolve_column("zone", target_table) or "zone_name"
        dist_col = self.registry.resolve_column("district", target_table) or "distname"
        state_col = self.registry.resolve_column("state", target_table) or "statename"

        # ── 2. Handle Sales Difference (Delta Comparison) ─────────────────────
        if intent == "sales_difference":
            # Difference between two companies
            if len(entities.get("company", [])) >= 2:
                comp_a, comp_b = entities["company"][0], entities["company"][1]
                rendered = self.formulas.render_sales_difference(
                    company_a=comp_a,
                    company_b=comp_b,
                    comname_col=comname_col,
                    sales_col=sales_col
                )
                return {
                    "expression": rendered["expression"],
                    "alias": rendered["alias"],
                    "requires_lag": False
                }
            
            # Difference between two fiscal years
            elif len(entities.get("year", [])) >= 2:
                yr_a, yr_b = entities["year"][0], entities["year"][1]
                period_col = self.registry.resolve_column("fiscal_year", target_table) or "fiscal_year"
                rendered = self.formulas.render_sales_difference(
                    period_a=yr_a,
                    period_b=yr_b,
                    period_col=period_col,
                    sales_col=sales_col
                )
                return {
                    "expression": rendered["expression"],
                    "alias": rendered["alias"],
                    "requires_lag": False
                }
            
            # Difference between two states
            elif len(entities.get("state", [])) >= 2:
                st_a, st_b = entities["state"][0], entities["state"][1]
                expr = (
                    f"ROUND((SUM(CASE WHEN {state_col} = '{st_a}' THEN {sales_col} ELSE 0 END) - "
                    f"SUM(CASE WHEN {state_col} = '{st_b}' THEN {sales_col} ELSE 0 END))::numeric, 2)"
                )
                alias = f"sales_diff_{st_a.lower()}_vs_{st_b.lower()}"
                return {
                    "expression": expr,
                    "alias": alias,
                    "requires_lag": False
                }

            # Fallback sales difference template
            return {
                "expression": f"SUM({sales_col})",
                "alias": "sales_volume",
                "requires_lag": False
            }

        # ── 3. Handle Growth % (YoY/MoM) ──────────────────────────────────────
        # YoY or MoM growth. If a specific year-over-year is queried for two specific years:
        if len(entities.get("year", [])) >= 2:
            yr_a, yr_b = entities["year"][0], entities["year"][1]
            # Order them chronologically (older is denominator)
            # e.g., 2024-2025 vs 2025-2026. "2024-2025" is smaller.
            ordered_yrs = sorted([yr_a, yr_b])
            older, newer = ordered_yrs[0], ordered_yrs[1]
            period_col = self.registry.resolve_column("fiscal_year", target_table) or "fiscal_year"
            
            expr = (
                f"ROUND(((SUM(CASE WHEN {period_col} = '{newer}' THEN {sales_col} ELSE 0 END) - "
                f"SUM(CASE WHEN {period_col} = '{older}' THEN {sales_col} ELSE 0 END)) * 100.0) / "
                f"NULLIF(SUM(CASE WHEN {period_col} = '{older}' THEN {sales_col} ELSE 0 END), 0), 2)"
            )
            alias = f"growth_pct_{older.replace('-', '_')}_to_{newer.replace('-', '_')}"
            return {
                "expression": expr,
                "alias": alias,
                "requires_lag": False
            }

        # Otherwise, use LAG window function to compute growth across periods
        # Determine partitioning columns (Company, SBU, Region, Zone, District, State)
        partition_cols = []
        for col in [comname_col, sbu_col, region_col, zone_col, dist_col, state_col]:
            if col in group_by:
                partition_cols.append(col)

        # Default partition if group by is empty
        if not partition_cols:
            partition_cols = [comname_col]

        # Determine ordering: default yearmonth for MoM, fiscal_year for YoY
        order_col = "yearmonth"
        if re.search(r"\byoy\b|\byear\b", q_lower):
            order_col = self.registry.resolve_column("fiscal_year", target_table) or "fiscal_year"

        rendered = self.formulas.render_growth(
            sales_col=sales_col,
            partition_col=", ".join(partition_cols),
            order_col=order_col
        )

        return {
            "expression": rendered["expression"],
            "alias": rendered["alias"],
            "requires_lag": True,
            "partition_columns": partition_cols,
            "order_column": order_col
        }


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_growth_planner() -> GrowthPlanner:
    return GrowthPlanner()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    planner = GrowthPlanner()

    print("=== Growth and Difference Planner Tests ===")

    # Test Case 1: Company Sales Difference
    print("\nTest Case 1: Company Difference (HPCL vs BPCL)")
    res1 = planner.plan_growth_or_difference(
        question="What is the difference in sales between HPCL and BPCL?",
        intent="sales_difference",
        target_table="industry_performance",
        group_by=[],
        entities={"company": ["HPCL", "BPCL"]}
    )
    print(f"  SQL Expression: {res1['expression']}")
    print(f"  Alias:          {res1['alias']}")
    print(f"  Requires Lag:   {res1['requires_lag']}")

    # Test Case 2: Specific Years YoY Growth
    print("\nTest Case 2: YoY Growth between two years (FY24-25 vs FY25-26)")
    res2 = planner.plan_growth_or_difference(
        question="Show growth between FY 2024-2025 and FY 2025-2026",
        intent="growth",
        target_table="industry_performance",
        group_by=[],
        entities={"year": ["2024-2025", "2025-2026"]}
    )
    print(f"  SQL Expression: {res2['expression']}")
    print(f"  Alias:          {res2['alias']}")
    print(f"  Requires Lag:   {res2['requires_lag']}")

    # Test Case 3: Monthly Growth with Group By Region
    print("\nTest Case 3: Trend Growth with Region Partition")
    res3 = planner.plan_growth_or_difference(
        question="Show month over month sales growth by region",
        intent="growth",
        target_table="industry_performance",
        group_by=["ro"],
        entities={}
    )
    print(f"  SQL Expression: {res3['expression']}")
    print(f"  Alias:          {res3['alias']}")
    print(f"  Requires Lag:   {res3['requires_lag']}")
