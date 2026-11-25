"""
NLPSQL SYSTEM — RANKING PLANNER
================================
Plans and normalises ORDER BY directions, LIMIT clauses, and grouping dimensions
for Top N / Bottom N / Highest / Lowest / Best / Worst queries.

Rules:
  - Superlatives ("highest", "lowest", "best", "worst") without numbers default to LIMIT 1.
  - "Top N" / "Bottom N" default to LIMIT N.
  - Ranking queries MUST have a grouping dimension in order to rank. If none are
    specified, maps them implicitly from the question text.
"""

from __future__ import annotations

import re
from typing import Dict, List, Any, Optional

from core.semantic_registry import get_registry


class RankingPlanError(ValueError):
    """Raised when a ranking plan violates mandatory rules."""
    pass


class RankingPlanner:
    """
    Plans and refines ORDER BY and LIMIT clauses for ranking intents.
    """

    def __init__(self):
        self.registry = get_registry()

    def plan_ranking(
        self,
        question: str,
        intent: str,
        target_table: str,
        group_by: List[str],
        metric_alias: str
    ) -> Dict[str, Any]:
        """
        Plans ranking options (order by, limit, group by refinements).

        Args:
            question: Original natural language question.
            intent: Primary detected intent (e.g. "top_n", "bottom_n").
            target_table: Resolved target table.
            group_by: Current list of group by columns.
            metric_alias: Select metric alias (to order by).

        Returns:
            Dict containing:
                "order_by": List of ORDER BY expressions (e.g. ["actual_sales_tmt DESC"])
                "limit": Integer LIMIT value (or None)
                "group_by": Refined list of grouping columns
        """
        q_lower = question.lower()
        refined_group_by = list(group_by)

        # ── 1. Determine Direction ────────────────────────────────────────────
        # Default direction based on intent
        direction = "DESC"
        if intent == "bottom_n" or any(w in q_lower for w in ["lowest", "worst", "bottom", "least", "poorest"]):
            direction = "ASC"

        # ── 2. Parse Limit N ──────────────────────────────────────────────────
        limit_val = None
        
        # Look for explicit digit following/preceding superlative/ranking words
        # e.g. "top 5", "bottom 3", "first 10", "lowest 5"
        m_num = re.search(r"\b(?:top|bottom|first|last|rank|lowest|highest|best|worst|lowest)\s*(\d+)\b", q_lower)
        if m_num:
            limit_val = int(m_num.group(1))
        else:
            # Check for singular superlatives indicating top 1 (e.g. "highest", "lowest", "best", "worst")
            singular_superlatives = ["highest", "lowest", "best", "worst", "maximum", "minimum", "max", "min", "least"]
            if any(re.search(rf"\b{w}\b", q_lower) for w in singular_superlatives):
                # E.g. "Which company had the highest sales?" -> limit 1
                limit_val = 1
            else:
                # Default fallback for Top N / Bottom N intents if no limit specified
                limit_val = 5

        # ── 3. Refine Grouping Columns ────────────────────────────────────────
        # If group_by is empty, ranking is impossible. We must infer grouping implicitly.
        if not refined_group_by:
            implicit_mappings = {
                "company": (r"\b(company|companies|omc|omcs|firms|firm)\b", "company"),
                "state": (r"\b(state|states)\b", "state"),
                "district": (r"\b(district|districts)\b", "district"),
                "region": (r"\b(region|regions|ro)\b", "region"),
                "zone": (r"\b(zone|zones)\b", "zone"),
                "product": (r"\b(product|products)\b", "product"),
                "sbu": (r"\b(sbu|sbus)\b", "sbu"),
            }
            
            inferred = False
            for concept, (pattern, reg_key) in implicit_mappings.items():
                if re.search(pattern, q_lower):
                    col = self.registry.resolve_group_by_column(reg_key, target_table)
                    if col:
                        refined_group_by.append(col)
                        inferred = True
                        break

            if not inferred:
                # Fallback to company grouping if absolutely no dimension could be inferred
                col = self.registry.resolve_group_by_column("company", target_table)
                if col:
                    refined_group_by.append(col)

        # ── 4. Construct ORDER BY ─────────────────────────────────────────────
        order_by_exprs = [f"{metric_alias} {direction}"]

        return {
            "order_by": order_by_exprs,
            "limit": limit_val,
            "group_by": refined_group_by
        }


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_ranking_planner() -> RankingPlanner:
    return RankingPlanner()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    planner = RankingPlanner()

    print("=== Ranking Planner Tests ===")

    # Test Case 1: Explicit Top 5
    print("\nTest Case 1: Top 5 Districts")
    res1 = planner.plan_ranking(
        question="Show top 5 districts by sales in FY 2024-2025",
        intent="top_n",
        target_table="industry_performance",
        group_by=["distname"],
        metric_alias="actual_sales_tmt"
    )
    print(f"  Order By: {res1['order_by']}")
    print(f"  Limit:    {res1['limit']}")
    print(f"  Group By: {res1['group_by']}")

    # Test Case 2: Implicit Singular Superlative ("highest")
    print("\nTest Case 2: Implicit Singular Superlative (highest company)")
    res2 = planner.plan_ranking(
        question="Which company had the highest sales?",
        intent="top_n",
        target_table="industry_performance",
        group_by=[], # Empty!
        metric_alias="actual_sales_tmt"
    )
    print(f"  Order By: {res2['order_by']}")
    print(f"  Limit:    {res2['limit']}")
    print(f"  Group By: {res2['group_by']}")

    # Test Case 3: Bottom N / Worst
    print("\nTest Case 3: Bottom 3 Worst States")
    res3 = planner.plan_ranking(
        question="Who are the bottom 3 worst states by sales?",
        intent="bottom_n",
        target_table="industry_performance",
        group_by=["statename"],
        metric_alias="actual_sales_tmt"
    )
    print(f"  Order By: {res3['order_by']}")
    print(f"  Limit:    {res3['limit']}")
    print(f"  Group By: {res3['group_by']}")
