"""
NLPSQL SYSTEM — BUSINESS CORRECTNESS VALIDATOR
==============================================
Enforces semantic correctness rules on PostgreSQL statements.

Rules enforced:
  1. Market Share breakdown queries MUST contain a GROUP BY company (comname) clause.
  2. Growth / Period Comparison queries MUST contain order partitions or period filter ranges.
  3. Ranking (Top/Bottom N) queries MUST contain an ORDER BY clause.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Any, Optional

import sqlglot
from sqlglot import exp


@dataclass
class BusinessValidationResult:
    is_correct: bool
    errors: List[str]

    def __repr__(self) -> str:
        return f"BusinessValidationResult(is_correct={self.is_correct}, errors={self.errors})"


class BusinessValidator:
    """
    Enforces business logic rules on target SQL expressions.
    """

    def validate_correctness(self, sql: str, intent: str) -> BusinessValidationResult:
        errors = []

        try:
            expression = sqlglot.parse_one(sql, read="postgres")
        except Exception as e:
            return BusinessValidationResult(
                is_correct=False,
                errors=[f"SQL Parse Error in Business Validator: {e}"]
            )

        sql_lower = sql.lower()

        # ── Rule 1: Market Share Group By Company ─────────────────────────────
        # If intent is market_share, and the query is a breakdown ("each company", "company wise" or "all companies")
        # OR if the query selects a general market share percentage alias without a specific company filter.
        is_market_share = (intent == "market_share" or "market_share_pct" in sql_lower)
        if is_market_share:
            # Check if there is a specific company filter (e.g. comname = 'HPCL')
            where_node = expression.find(exp.Where)
            has_company_filter = False
            if where_node:
                where_sql = where_node.sql().lower()
                # If there's an equal or IN operator with a specific OMC
                if "comname =" in where_sql or "comname in" in where_sql:
                    has_company_filter = True

            # If there's no single-company filter, we MUST have GROUP BY comname
            if not has_company_filter:
                group_node = expression.find(exp.Group)
                has_company_group = False
                if group_node:
                    for g_expr in group_node.expressions:
                        if isinstance(g_expr, exp.Column) and g_expr.name.lower() == "comname":
                            has_company_group = True
                
                if not has_company_group:
                    errors.append(
                        "Business Rule Violation: Market share breakdown queries must group by company ('comname')."
                    )

        # ── Rule 2: Growth period comparison ──────────────────────────────────
        is_growth = (intent == "growth" or "growth_pct" in sql_lower or "lag(" in sql_lower)
        if is_growth:
            # Must either contain ordering on yearmonth/fiscal_year inside a window function:
            has_window_order = False
            for window in expression.find_all(exp.Window):
                window_sql = window.sql().lower()
                if "order by" in window_sql and any(c in window_sql for c in ["yearmonth", "fiscal_year"]):
                    has_window_order = True

            # OR contain multiple periods compared in conditional aggregates (e.g. SUM(CASE WHEN fiscal_year='...'))
            has_conditional_periods = False
            where_node = expression.find(exp.Where)
            if where_node:
                where_sql = where_node.sql().lower()
                # Check if multiple years are filtered
                years_matches = re.findall(r"'\d{4}\-\d{4}'|\b20\d{2}\b", where_sql)
                if len(years_matches) >= 2 or "between" in where_sql:
                    has_conditional_periods = True

            # If it's a dynamic lag growth and has no window order, OR if it's conditional difference but has only 1 period
            if not has_window_order and not has_conditional_periods:
                errors.append(
                    "Business Rule Violation: Growth queries must contain time ordering partitions or filter multiple periods."
                )

        # ── Rule 3: Ranking query must contain ordering ──────────────────────
        is_ranking = (intent in ("top_n", "bottom_n") or "limit" in sql_lower)
        if is_ranking:
            order_node = expression.find(exp.Order)
            if not order_node:
                errors.append(
                    "Business Rule Violation: Ranking queries (Top/Bottom N) must contain an ORDER BY clause."
                )

        return BusinessValidationResult(
            is_correct=(len(errors) == 0),
            errors=errors
        )


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_business_validator() -> BusinessValidator:
    return BusinessValidator()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    validator = BusinessValidator()

    print("=== Business Correctness Validator Tests ===")

    # Test 1: Market Share breakdown without Group By
    print("\n1. Market Share without Group By:")
    sql1 = "SELECT ROUND((SUM(netweight_tmt)*100.0)/SUM(SUM(netweight_tmt)) OVER(), 2) AS market_share_pct FROM industry_performance WHERE category != 'O';"
    res1 = validator.validate_correctness(sql1, "market_share")
    print(f"  Is Correct: {res1.is_correct}")
    print(f"  Errors:     {res1.errors}")

    # Test 2: Market Share breakdown WITH Group By (Correct)
    print("\n2. Market Share WITH Group By:")
    sql2 = "SELECT comname, ROUND((SUM(netweight_tmt)*100.0)/SUM(SUM(netweight_tmt)) OVER(), 2) AS market_share_pct FROM industry_performance WHERE category != 'O' GROUP BY comname;"
    res2 = validator.validate_correctness(sql2, "market_share")
    print(f"  Is Correct: {res2.is_correct}")
    print(f"  Errors:     {res2.errors}")

    # Test 3: Growth without periods
    print("\n3. Growth without Time Order/Filters:")
    sql3 = "SELECT comname, ROUND(SUM(netweight_tmt), 2) AS growth_pct FROM industry_performance WHERE category != 'O' GROUP BY comname;"
    res3 = validator.validate_correctness(sql3, "growth")
    print(f"  Is Correct: {res3.is_correct}")
    print(f"  Errors:     {res3.errors}")

    # Test 4: Ranking without Order By
    print("\n4. Ranking without Order By:")
    sql4 = "SELECT distname FROM industry_performance WHERE category != 'O' LIMIT 5;"
    res4 = validator.validate_correctness(sql4, "top_n")
    print(f"  Is Correct: {res4.is_correct}")
    print(f"  Errors:     {res4.errors}")
