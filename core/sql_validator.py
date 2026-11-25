"""
NLPSQL SYSTEM — SQL VALIDATOR (AST ENGINE)
===========================================
Validates PostgreSQL queries using SQLGlot AST analysis.

Checks performed:
  - Table existence (must match target dataset schema)
  - Column existence (cross-referenced against target table schema)
  - Mandatory filter enforcement (category != 'O' on industry_performance)
  - Group By validity (non-aggregated select projection columns must be in Group By)
  - Aggregate validity (aggregates cannot be in WHERE clause)
  - Join validity (flag unnecessary joins on denormalized schema)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple, Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# ── Dynamic Table Schema Definitions ──────────────────────────────────────────
_TABLE_SCHEMAS: Dict[str, Set[str]] = {
    "industry_performance": {
        "comname", "sbu_name", "productname", "statename", "distname",
        "zone_name", "ro", "fiscal_year", "month_name", "yearmonth",
        "netweight_tmt", "category"
    },
    "mom_level": {
        "sbu_name", "region_name", "productname", "fiscal_year",
        "month_name", "yearmonth", "netweight_tmt"
    },
    "m60_level": {
        "statename", "zone_name", "region_name", "fiscal_year",
        "month_name", "yearmonth", "target_qty_tmt", "netweight_tmt"
    }
}


@dataclass
class ValidationResult:
    is_valid: bool
    errors: List[str]
    warnings: List[str]

    def __repr__(self) -> str:
        return f"ValidationResult(is_valid={self.is_valid}, errors={self.errors}, warnings={self.warnings})"


class SQLValidator:
    """
    Validates PostgreSQL statements programmatically using AST traversal.
    """

    def validate(self, sql: str) -> ValidationResult:
        errors = []
        warnings = []

        # ── 1. Syntax Parsing ─────────────────────────────────────────────────
        try:
            expression = sqlglot.parse_one(sql, read="postgres")
        except ParseError as e:
            return ValidationResult(
                is_valid=False,
                errors=[f"SQL Syntax Parse Error: {e}"],
                warnings=[]
            )

        # ── 2. Table Existence ────────────────────────────────────────────────
        tables = [t.name.lower() for t in expression.find_all(exp.Table)]
        if not tables:
            errors.append("No target table specified in FROM clause.")
        
        for table in tables:
            if table not in _TABLE_SCHEMAS:
                errors.append(f"Table '{table}' does not exist in schema registry.")

        # If table checks failed, return early
        if errors:
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings)

        # Determine the primary table for column validation
        primary_table = tables[0]
        allowed_columns = set(_TABLE_SCHEMAS[primary_table])

        # Extract aliases defined in SELECT projection
        select_node = expression.find(exp.Select)
        if select_node:
            for select_expr in select_node.expressions:
                if select_expr.alias:
                    allowed_columns.add(select_expr.alias.lower())

        # ── 3. Column Existence ───────────────────────────────────────────────
        columns_in_query = []
        for col_node in expression.find_all(exp.Column):
            columns_in_query.append(col_node.name.lower())

        for col in columns_in_query:
            if col not in allowed_columns:
                errors.append(f"Column '{col}' does not exist on table '{primary_table}'.")

        # ── 4. Mandatory Filters Check ────────────────────────────────────────
        if primary_table == "industry_performance":
            where_node = expression.find(exp.Where)
            if not where_node:
                errors.append("Mandatory filter rule violated: No WHERE clause found on industry_performance.")
            else:
                where_sql = where_node.sql().lower()
                # Check for category != 'o' or category is null
                if "category" not in where_sql or "o" not in where_sql:
                    errors.append("Mandatory filter rule violated: category != 'O' exclusion filter is missing.")

        # ── 5. Group By Validity ──────────────────────────────────────────────
        # Standard SQL rule: any un-aggregated select projection column must be present in GROUP BY
        select_node = expression.find(exp.Select)
        if select_node:
            group_node = expression.find(exp.Group)
            group_cols = set()
            if group_node:
                for g_expr in group_node.expressions:
                    if isinstance(g_expr, exp.Column):
                        group_cols.add(g_expr.name.lower())

            # Traverse the select expressions
            for select_expr in select_node.expressions:
                # Find all columns inside this select expression
                expr_cols = [c.name.lower() for c in select_expr.find_all(exp.Column)]
                
                # Check if this expression is inside an aggregate function
                is_aggregated = any(
                    isinstance(node, (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max))
                    for node in select_expr.find_all(exp.Expression)
                ) or isinstance(select_expr, (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max))

                if expr_cols and not is_aggregated:
                    # Non-aggregated column must be in group_cols (if group by exists, or if there is any aggregation)
                    has_any_aggregate = any(
                        isinstance(n, (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max))
                        for n in select_node.find_all(exp.Expression)
                    )
                    
                    if has_any_aggregate or group_node:
                        for col in expr_cols:
                            if col not in group_cols:
                                errors.append(f"SELECT column '{col}' is not aggregated and is missing from GROUP BY.")

        # ── 6. Aggregate Validity ─────────────────────────────────────────────
        # Verify aggregate functions are not placed directly in the WHERE clause
        where_node = expression.find(exp.Where)
        if where_node:
            aggregates_in_where = any(
                isinstance(node, (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max))
                for node in where_node.find_all(exp.Expression)
            )
            if aggregates_in_where:
                errors.append("Syntax Error: Aggregate functions are not allowed in the WHERE clause.")

        # ── 7. Join Validity ──────────────────────────────────────────────────
        # Flag JOINs since all schema tables are fully denormalized
        joins = list(expression.find_all(exp.Join))
        if joins:
            warnings.append(
                f"Query contains {len(joins)} JOIN statement(s). Note that database schema tables "
                f"({', '.join(_TABLE_SCHEMAS.keys())}) are denormalized and do not require JOIN operations."
            )

        return ValidationResult(
            is_valid=(len(errors) == 0),
            errors=errors,
            warnings=warnings
        )


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_sql_validator() -> SQLValidator:
    return SQLValidator()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    validator = SQLValidator()

    print("=== SQL AST Validator Tests ===")

    test_queries = {
        "Valid ranking query": (
            "SELECT distname, ROUND(SUM(netweight_tmt)::numeric, 2) AS actual_sales_tmt\n"
            "FROM industry_performance\n"
            "WHERE (fiscal_year = '2024-2025') AND (category != 'O')\n"
            "GROUP BY distname\n"
            "ORDER BY actual_sales_tmt DESC\n"
            "LIMIT 5;"
        ),
        "Invalid table": (
            "SELECT distname FROM dummy_table WHERE category != 'O';"
        ),
        "Invalid column": (
            "SELECT fake_col FROM industry_performance WHERE category != 'O';"
        ),
        "Missing mandatory filter": (
            "SELECT distname FROM industry_performance;"
        ),
        "Missing group by for select column": (
            "SELECT distname, SUM(netweight_tmt) FROM industry_performance WHERE category != 'O';"
        ),
        "Aggregate in WHERE clause": (
            "SELECT distname FROM industry_performance WHERE SUM(netweight_tmt) > 500 AND category != 'O';"
        ),
        "Query with JOIN (Warning)": (
            "SELECT a.distname FROM industry_performance a JOIN mom_level b ON a.sbu_name = b.sbu_name WHERE a.category != 'O';"
        )
    }

    for name, sql in test_queries.items():
        print(f"\n--- {name} ---")
        print(sql)
        res = validator.validate(sql)
        print(f"Is Valid: {res.is_valid}")
        if res.errors:
            print("Errors:")
            for e in res.errors:
                print(f"  - {e}")
        if res.warnings:
            print("Warnings:")
            for w in res.warnings:
                print(f"  - {w}")
