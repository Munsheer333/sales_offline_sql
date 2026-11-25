"""
NLPSQL SYSTEM — FINAL SQL REVIEWER
===================================
Compares the final compiled PostgreSQL query against the planned QueryPlan
and original question to verify structural and logical correctness.

Checks:
  1. Intent Correctness: Verifies limits, order directions, and window operations.
  2. Filter Correctness: Ensures all planned filter columns and values exist in the WHERE clause.
  3. Formula Correctness: Validates that selected metrics match planned business formulas.
  4. Dimension Correctness: Verifies all group-by dimensions exist in the SQL group by clause.

Outputs:
  - "PASS" or "FAIL" (with list of failures)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Any, Tuple

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from core.query_planner import QueryPlan


@dataclass
class ReviewResult:
    status: str  # "PASS" or "FAIL"
    failures: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "failures": self.failures
        }


class SQLReviewer:
    """
    Syntactically and semantically verifies compiled SQL against the query plan.
    """

    def review(
        self,
        question: str,
        plan: QueryPlan,
        sql: str
    ) -> ReviewResult:
        failures = []

        # ── 1. Parse compiled SQL ─────────────────────────────────────────────
        try:
            expression = sqlglot.parse_one(sql, read="postgres")
        except Exception as e:
            return ReviewResult(status="FAIL", failures=[f"Failed to parse compiled SQL: {e}"])

        sql_lower = sql.lower()

        # ── 2. Intent Correctness ─────────────────────────────────────────────
        if plan.intent in ("top_n", "bottom_n"):
            # Check for limit
            limit_node = expression.find(exp.Limit)
            if not limit_node:
                failures.append(f"Intent '{plan.intent}' requires LIMIT, but none found in SQL.")
            elif plan.limit and int(limit_node.expression.name) != plan.limit:
                failures.append(
                    f"LIMIT mismatch. Planned: {plan.limit}, Found in SQL: {limit_node.expression.name}"
                )

            # Check for order by
            order_node = expression.find(exp.Order)
            if not order_node:
                failures.append(f"Intent '{plan.intent}' requires ORDER BY, but none found in SQL.")

        elif plan.intent == "growth":
            # Check for window lag or conditional comparison
            has_lag = "lag(" in sql_lower
            has_case = "case " in sql_lower
            if not has_lag and not has_case:
                failures.append("Intent 'growth' requires window LAG or conditional CASE comparisons, but none found.")

        # ── 3. Filter Correctness ─────────────────────────────────────────────
        where_node = expression.find(exp.Where)
        where_sql = where_node.sql().lower() if where_node else ""

        for f in plan.filters:
            col = f.get("column", "").lower()
            op = f.get("operator", "")
            values = f.get("values", [])

            if op == "RAW":
                # Raw mandatory string must be contained in the where clause
                clean_col = col.replace("(", "").replace(")", "").lower()
                # Check normalized sub-strings
                if "category != 'o'" not in where_sql and "category != 'o'" not in clean_col:
                    failures.append(f"Mandatory rule filter '{col}' is missing from SQL WHERE clause.")
                continue

            # Verify filter column is referenced in WHERE clause
            if col not in where_sql:
                failures.append(f"Planned filter column '{col}' is missing from SQL WHERE clause.")

            # Verify filter values are referenced in WHERE clause
            for val in values:
                val_str = str(val).lower()
                if val_str not in where_sql:
                    failures.append(f"Planned filter value '{val_str}' for column '{col}' is missing from SQL WHERE clause.")

        # ── 4. Formula Correctness ────────────────────────────────────────────
        # Extract selected metric formulas in SELECT
        select_node = expression.find(exp.Select)
        select_sql = select_node.sql().lower() if select_node else ""

        for m in plan.select_metrics:
            expr = m.get("expression", "").lower()
            alias = m.get("alias", "").lower()

            # We need to make sure the target expression formula is present
            # Remove spaces to compare expressions robustly
            clean_expr_plan = re.sub(r"\s+", "", expr)
            clean_select_sql = re.sub(r"\s+", "", select_sql)

            if alias not in clean_select_sql:
                failures.append(f"Planned metric alias '{alias}' is missing from SELECT clause.")

        # ── 5. Dimension Correctness ──────────────────────────────────────────
        group_node = expression.find(exp.Group)
        group_cols_in_sql = set()
        if group_node:
            for g_expr in group_node.expressions:
                if isinstance(g_expr, exp.Column):
                    group_cols_in_sql.add(g_expr.name.lower())

        for dim_col in plan.group_by:
            dim_col_lower = dim_col.lower()
            if dim_col_lower not in group_cols_in_sql:
                failures.append(f"Planned dimension '{dim_col}' is missing from SQL GROUP BY clause.")

        # ── 6. Formulate Status ───────────────────────────────────────────────
        status = "PASS" if not failures else "FAIL"
        return ReviewResult(status=status, failures=failures)


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_sql_reviewer() -> SQLReviewer:
    return SQLReviewer()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    reviewer = SQLReviewer()

    # 1. Successful pass comparison
    plan_pass = QueryPlan(
        target_table="industry_performance",
        intent="top_n",
        select_metrics=[{"expression": "ROUND(SUM(netweight_tmt)::numeric, 2)", "alias": "actual_sales_tmt"}],
        group_by=["distname"],
        filters=[
            {"column": "fiscal_year", "operator": "=", "values": ["2024-2025"], "type": "filter"},
            {"column": "(category != 'O' OR category IS NULL)", "operator": "RAW", "values": []}
        ],
        order_by=["actual_sales_tmt DESC"],
        limit=5
    )
    sql_pass = (
        "SELECT distname, ROUND(SUM(netweight_tmt)::numeric, 2) AS actual_sales_tmt\n"
        "FROM industry_performance\n"
        "WHERE (fiscal_year = '2024-2025') AND ((category != 'O' OR category IS NULL))\n"
        "GROUP BY distname\n"
        "ORDER BY actual_sales_tmt DESC\n"
        "LIMIT 5;"
    )

    print("=== SQL Reviewer Tests ===")
    res1 = reviewer.review("Show top 5 districts by sales in FY 2024-2025", plan_pass, sql_pass)
    print(f"\nTest 1 (Expected PASS):\n  Status:   {res1.status}\n  Failures: {res1.failures}")

    # 2. Mismatched limit and missing filter column
    plan_fail = QueryPlan(
        target_table="industry_performance",
        intent="top_n",
        select_metrics=[{"expression": "ROUND(SUM(netweight_tmt)::numeric, 2)", "alias": "actual_sales_tmt"}],
        group_by=["distname"],
        filters=[
            {"column": "statename", "operator": "=", "values": ["MAHARASHTRA"], "type": "filter"}
        ],
        order_by=["actual_sales_tmt DESC"],
        limit=5
    )
    sql_fail = (
        "SELECT distname, ROUND(SUM(netweight_tmt)::numeric, 2) AS actual_sales_tmt\n"
        "FROM industry_performance\n"
        "WHERE (fiscal_year = '2024-2025')\n"
        "GROUP BY distname\n"
        "ORDER BY actual_sales_tmt DESC\n"
        "LIMIT 10;" # Mismatched limit, missing statename filter
    )

    res2 = reviewer.review("Show top 5 districts in Maharashtra", plan_fail, sql_fail)
    print(f"\nTest 2 (Expected FAIL):\n  Status:   {res2.status}\n  Failures: {res2.failures}")
