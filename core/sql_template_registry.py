"""
NLPSQL SYSTEM — SQL TEMPLATE REGISTRY
======================================
Loads and compiles target SQL statements based on blueprints in
metadata/sql_templates.json.

Isolates SQL dialect construction from prompt templates.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Any, Optional

_TEMPLATES_PATH = Path(__file__).resolve().parent.parent / "metadata" / "sql_templates.json"


class SQLTemplateRegistry:
    """
    Programmatically loads and compiles SQL query blocks using intent-specific templates.
    """

    def __init__(self, path: Path = _TEMPLATES_PATH):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self._templates: Dict[str, Dict[str, str]] = raw.get("templates", {})

    def get_template_string(self, intent: str) -> str:
        """
        Retrieves the query template string for the specified intent.
        """
        mapping = {
            "total_sales": "total_sales",
            "actual_volume": "total_sales",
            "market_share": "market_share",
            "growth": "growth",
            "sales_difference": "total_sales",
            "comparison": "comparison",
            "top_n": "ranking",
            "bottom_n": "ranking",
            "trend": "trend"
        }
        name = mapping.get(intent, "total_sales")
        template_dict = self._templates.get(name, self._templates["total_sales"])
        return template_dict.get("query", "")

    def compile(
        self,
        intent: str,
        table_name: str,
        metric_expr: str,
        metric_alias: str,
        group_by_cols: List[str],
        where_clauses: List[str],
        order_by_cols: List[str],
        limit_val: Optional[int] = None
    ) -> str:
        """
        Compiles standard structural SQL blocks using templates.
        """
        query_tmpl = self.get_template_string(intent)

        # ── 1. Select Columns ─────────────────────────────────────────────────
        group_by_clause_prefix = ""
        if group_by_cols:
            group_by_clause_prefix = ", ".join(group_by_cols) + ", "
        select_cols = f"{group_by_clause_prefix}{metric_expr} AS {metric_alias}"

        # ── 2. Where Clause ───────────────────────────────────────────────────
        where_clause = ""
        if where_clauses:
            where_clause = " WHERE " + " AND ".join(f"({c})" for c in where_clauses)

        # ── 3. Group By Clause ────────────────────────────────────────────────
        group_by_clause = ""
        if group_by_cols:
            group_by_clause = " GROUP BY " + ", ".join(group_by_cols)

        # ── 4. Order By Clause ────────────────────────────────────────────────
        order_by_clause = ""
        if order_by_cols:
            order_by_clause = " ORDER BY " + ", ".join(order_by_cols)
        elif intent == "trend":
            # Default trend ordering: find time column in group_by
            time_cols = [c for c in group_by_cols if c in ("yearmonth", "month_name", "fiscal_year")]
            if time_cols:
                order_by_clause = " ORDER BY " + ", ".join(time_cols)

        # ── 5. Limit Clause ───────────────────────────────────────────────────
        limit_clause = ""
        if limit_val is not None:
            limit_clause = f" LIMIT {limit_val}"

        # Format template
        return query_tmpl.format(
            select_cols=select_cols,
            table_name=table_name,
            where_clause=where_clause,
            group_by_clause=group_by_clause,
            order_by_clause=order_by_clause,
            limit_clause=limit_clause
        )


# ── Module-level singleton ────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_sql_template_registry() -> SQLTemplateRegistry:
    return SQLTemplateRegistry()
