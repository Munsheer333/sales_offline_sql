"""
NLPSQL SYSTEM — SQL GENERATOR
=============================
Assembles PostgreSQL query statements programmatically from a QueryPlan.

Uses intent-specific block patterns from the SQLTemplateRegistry to construct
valid syntax deterministically. Never uses LLMs or free-form text blocks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Any, Optional

from core.sql_template_registry import get_sql_template_registry
from core.query_planner import QueryPlan


class SQLGenerator:
    """
    Translates a QueryPlan into syntactically valid PostgreSQL statements.
    """

    def __init__(self):
        self.template_registry = get_sql_template_registry()

    def _compile_filter(self, f: Dict[str, Any]) -> Optional[str]:
        """Translates a structured filter plan item into a SQL fragment."""
        concept = f.get("concept")
        col = f.get("column")
        op = f.get("operator")
        values = f.get("values", [])

        if not col:
            return None

        # 1. RAW/Mandatory Rules
        if op == "RAW":
            return col

        if not values:
            return None

        # 2. Standard operator mappings
        # Clean and quote values
        cleaned_vals = []
        for val in values:
            if isinstance(val, (int, float)):
                cleaned_vals.append(str(val))
            else:
                # Escape single quotes
                escaped = str(val).replace("'", "''")
                cleaned_vals.append(f"'{escaped}'")

        if op == "=":
            return f"{col} = {cleaned_vals[0]}"
        elif op == "IN":
            val_list = ", ".join(cleaned_vals)
            return f"{col} IN ({val_list})"
        elif op == "BETWEEN" and len(cleaned_vals) >= 2:
            return f"{col} BETWEEN {cleaned_vals[0]} AND {cleaned_vals[1]}"

        return None

    def generate(self, plan: QueryPlan) -> str:
        """
        Compiles the logical QueryPlan into the final PostgreSQL query string.

        Args:
            plan: The planned QueryPlan object.

        Returns:
            Formatted PostgreSQL statement.
        """
        # 1. Compile Select Metric expression & alias
        metric_expr = ""
        metric_alias = "actual_sales_tmt"
        if plan.select_metrics:
            metric_expr = plan.select_metrics[0].get("expression", "")
            metric_alias = plan.select_metrics[0].get("alias", "")

        # 2. Compile Where clauses
        where_clauses = []
        for f in plan.filters:
            fragment = self._compile_filter(f)
            if fragment:
                where_clauses.append(fragment)

        # 3. Assemble target table, order by, and limit parameters
        table_name = plan.target_table
        group_by_cols = plan.group_by
        order_by_cols = plan.order_by
        limit_val = plan.limit

        # 4. Invoke template engine compiler
        query = self.template_registry.compile(
            intent=plan.intent,
            table_name=table_name,
            metric_expr=metric_expr,
            metric_alias=metric_alias,
            group_by_cols=group_by_cols,
            where_clauses=where_clauses,
            order_by_cols=order_by_cols,
            limit_val=limit_val
        )

        return query


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_sql_generator() -> SQLGenerator:
    return SQLGenerator()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    generator = SQLGenerator()

    # Load planned json directly for a mock test
    # Define a QueryPlan manually
    plan_mock = QueryPlan(
        target_table="industry_performance",
        intent="top_n",
        select_metrics=[{
            "expression": "ROUND(SUM(netweight_tmt)::numeric, 2)",
            "alias": "actual_sales_tmt"
        }],
        group_by=["distname"],
        filters=[
            {
                "concept": "fiscal_year",
                "column": "fiscal_year",
                "operator": "=",
                "values": ["2024-2025"],
                "type": "filter"
            },
            {
                "concept": "mandatory_rule",
                "column": "(category != 'O' OR category IS NULL)",
                "operator": "RAW",
                "values": [],
                "type": "mandatory_filter"
            }
        ],
        order_by=["actual_sales_tmt DESC"],
        limit=5
    )

    print("=== SQL Generator Compiler Tests ===")
    sql = generator.generate(plan_mock)
    print(f"\nGenerated PostgreSQL SQL:\n{sql}")
