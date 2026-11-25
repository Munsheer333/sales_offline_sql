"""
NLPSQL SYSTEM — MARKET SHARE PLANNER & VALIDATOR
=================================================
Validates and plans market share calculations programmatically.

Rules:
  - Formula: (company sales / total market sales) * 100
  - If "each company", "company-wise", or "all companies" is detected:
      - GROUP BY company column is MANDATORY.
      - If grouping is missing, the plan must be REJECTED.
  - Formulates window aggregates OVER() or conditional sums dynamically.
"""

from __future__ import annotations

import re
from typing import Dict, List, Any, Optional

from core.semantic_registry import get_registry
from core.formula_registry import get_formula_registry


class MarketSharePlanError(ValueError):
    """Raised when a market share plan violates mandatory rules."""
    pass


class MarketSharePlanner:
    """
    Validates and overrides the query plan for market share queries.
    """

    def __init__(self):
        self.registry = get_registry()
        self.formulas = get_formula_registry()

    def plan_market_share(
        self,
        question: str,
        target_table: str,
        group_by: List[str],
        companies_filtered: List[str],
        sales_col: str = "netweight_tmt"
    ) -> Dict[str, Any]:
        """
        Validates grouping rules and plans the exact SQL expression for market share.

        Args:
            question: Original natural language query.
            target_table: Routed target table.
            group_by: Current list of planned group by columns.
            companies_filtered: List of companies extracted for filtering.
            sales_col: Column containing sales volume.

        Returns:
            Dict containing the updated select_metric expression, alias, and group_by.
        """
        q_lower = question.lower()
        comname_col = self.registry.resolve_column("company", target_table) or "comname"

        # ── 1. Detect Company-Wise Breakdown Mentions ─────────────────────────
        company_breakdown_signals = [
            r"\beach\s*compan(y|ies)\b",
            r"\bcompan(y|ies)\s*wise\b",
            r"\bby\s*compan(y|ies)\b",
            r"\bper\s*compan(y|ies)\b",
            r"\bcompan(y|ies)\-wise\b",
            r"\ball\s*compan(y|ies)\b",
            r"\bshare\s*of\s*(each|all)\s*compan(y|ies)\b",
            r"\bmarket\s*share\s*of\s*(each|all)\b",
        ]
        
        is_breakdown = any(re.search(sig, q_lower) for sig in company_breakdown_signals)

        # ── 2. Enforce Mandatory Group By for Breakdown queries ────────────────
        if is_breakdown:
            if comname_col not in group_by:
                # Reject plan as per prompt requirement
                raise MarketSharePlanError(
                    f"Market share breakdown requested, but company grouping column '{comname_col}' is missing "
                    f"from group_by dimensions. Grouping by company is mandatory for breakdown queries."
                )
            
            # Render company-wise breakdown formula: ROUND((SUM(sales) * 100.0) / SUM(SUM(sales)) OVER(), 2)
            rendered = self.formulas.render_market_share(
                comname_col=comname_col,
                sales_col=sales_col
            )
        else:
            # Single or specific company target market share
            # Render HPCL market share: ROUND((SUM(CASE WHEN comname='HPCL' THEN sales ELSE 0 END) * 100.0) / SUM(sales), 2)
            target_company = companies_filtered[0] if companies_filtered else None
            if not target_company:
                # If no company is filtered, default to overall breakdown if we have group_by
                if comname_col in group_by:
                    rendered = self.formulas.render_market_share(
                        comname_col=comname_col,
                        sales_col=sales_col
                    )
                else:
                    raise MarketSharePlanError(
                        "Market share query detected, but no specific company filter nor company grouping was planned."
                    )
            else:
                rendered = self.formulas.render_market_share(
                    company=target_company,
                    comname_col=comname_col,
                    sales_col=sales_col
                )

        return {
            "expression": rendered["expression"],
            "alias": rendered["alias"],
            "group_by": group_by
        }


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_market_share_planner() -> MarketSharePlanner:
    return MarketSharePlanner()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    planner = MarketSharePlanner()

    print("=== Market Share Planner Tests ===")

    # Test Case 1: Specific Company Market Share (no grouping needed)
    print("\nTest Case 1: Specific Company (HPCL)")
    try:
        res = planner.plan_market_share(
            question="What is the market share of HPCL in FY 2024-2025?",
            target_table="industry_performance",
            group_by=[],
            companies_filtered=["HPCL"]
        )
        print(f"  SQL Expression: {res['expression']}")
        print(f"  Alias:          {res['alias']}")
        print(f"  Group By:       {res['group_by']}")
    except Exception as e:
        print(f"  ❌ Error: {e}")

    # Test Case 2: Breakdown requested, grouping is PRESENT (valid)
    print("\nTest Case 2: Breakdown requested with grouping (valid)")
    try:
        res = planner.plan_market_share(
            question="Show market share of each company",
            target_table="industry_performance",
            group_by=["comname"],
            companies_filtered=[]
        )
        print(f"  SQL Expression: {res['expression']}")
        print(f"  Alias:          {res['alias']}")
        print(f"  Group By:       {res['group_by']}")
    except Exception as e:
        print(f"  ❌ Error: {e}")

    # Test Case 3: Breakdown requested, grouping is MISSING (should be REJECTED)
    print("\nTest Case 3: Breakdown requested without grouping (invalid)")
    try:
        res = planner.plan_market_share(
            question="Show market share of each company",
            target_table="industry_performance",
            group_by=[],
            companies_filtered=[]
        )
        print(f"  SQL Expression: {res['expression']}")
    except MarketSharePlanError as e:
        print(f"  ✅ Correctly Rejected: {e}")
    except Exception as e:
        print(f"  ❌ Wrong Error: {e}")
