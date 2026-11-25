"""
NLPSQL SYSTEM — BUSINESS RULE ENGINE
====================================
Programmatically enforces mandatory database constraints and rules
without using LLMs.

Rules enforced:
  - Exclude category 'O' rows from industry_performance queries:
      "category != 'O' OR category IS NULL"
  - Exclude legacy/internal SBUs from mom_level queries:
      "sbu_name != '0' AND sbu_name NOT IN ('Common', 'Mumbai Ref', 'Renewable Energy', 'Visakh Ref')"
  - Default Fiscal Year:
      If a query has no fiscal year filter planned, inject the CURRENT_FINANCIAL_YEAR.
"""

from __future__ import annotations

from typing import Dict, List, Any, Optional

from core.semantic_registry import get_registry
from core.fiscal_year_resolver import get_fiscal_year_resolver


class BusinessRuleEngine:
    """
    Enforces table-specific mandatory filters and business rules deterministically.
    """

    def __init__(self):
        self.registry = get_registry()
        self.fy_resolver = get_fiscal_year_resolver()

    def enforce(self, table_name: str, planned_filters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Enforces business rules on a planned list of filters.

        Args:
            table_name: The target SQL table name (e.g. "industry_performance").
            planned_filters: The list of filters from the ConditionalFilterPlanner.

        Returns:
            A new list of filters containing mandatory filters and default value injections.
        """
        enforced = list(planned_filters)

        # ── Rule 1: Inject Mandatory Filters from Semantic Registry ───────────
        # Get registry rules (e.g. category != 'O' or SBU exclusions)
        mand_sqls = self.registry.mandatory_filters(table_name)
        for sql in mand_sqls:
            # Check if this rule is already present to prevent duplicates
            rule_exists = any(
                f.get("concept") == "mandatory_rule" and f.get("column") == sql
                for f in enforced
            )
            if not rule_exists:
                enforced.append({
                    "concept": "mandatory_rule",
                    "column": sql,
                    "operator": "RAW",
                    "values": [],
                    "type": "mandatory_filter"
                })

        # ── Rule 2: Default Fiscal Year Injection ─────────────────────────────
        # Check if query already has a fiscal_year or yearmonth filter planned
        has_year_filter = any(
            f.get("concept") in ("fiscal_year", "yearmonth")
            for f in enforced
        )
        
        # Also check if it's the industry_performance, mom_level, or m60_level table
        if not has_year_filter and table_name in ("industry_performance", "mom_level", "m60_level"):
            col = self.registry.resolve_column("fiscal_year", table_name)
            if col:
                default_fy = self.fy_resolver.get_current_fy()
                enforced.append({
                    "concept": "fiscal_year",
                    "column": col,
                    "operator": "=",
                    "values": [default_fy],
                    "type": "filter",
                    "note": "Automatically injected default fiscal year"
                })

        return enforced


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_business_rule_engine() -> BusinessRuleEngine:
    return BusinessRuleEngine()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    engine = BusinessRuleEngine()

    print("=== Rule Enforcement Tests ===")

    # Test Case 1: Empty filters on industry_performance (should inject category filter and default FY)
    print("\nTest Case 1: Empty filters on industry_performance")
    filters_1 = []
    res_1 = engine.enforce("industry_performance", filters_1)
    for f in res_1:
        print(f"  Concept: {f['concept']:15s} | Column/SQL: {f['column']:55s} | Values: {f['values']}")

    # Test Case 2: Query already has a fiscal year planned (should only inject category filter)
    print("\nTest Case 2: Query with pre-planned fiscal year")
    filters_2 = [{
        "concept": "fiscal_year",
        "column": "fiscal_year",
        "operator": "=",
        "values": ["2024-2025"],
        "type": "filter"
    }]
    res_2 = engine.enforce("industry_performance", filters_2)
    for f in res_2:
        print(f"  Concept: {f['concept']:15s} | Column/SQL: {f['column']:55s} | Values: {f['values']}")

    # Test Case 3: Empty filters on mom_level (should inject SBU exclusion and default FY)
    print("\nTest Case 3: Empty filters on mom_level")
    filters_3 = []
    res_3 = engine.enforce("mom_level", filters_3)
    for f in res_3:
        print(f"  Concept: {f['concept']:15s} | Column/SQL: {f['column']:55s} | Values: {f['values']}")
