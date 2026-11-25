"""
NLPSQL SYSTEM — METRIC RESOLVER
================================
Resolves natural language metric mentions and unit conversion requests into
canonical database aggregation expressions.

Concepts supported:
  - "sales", "actual sales", "business", "volume" -> netweight_tmt
  - "target", "budget", "plan" -> target_qty_tmt

Units supported:
  - Default: TMT (Thousand Metric Tonnes) -> SUM(col)
  - Requested: MT (Metric Tonnes) / "tonnes" -> SUM(col * 1000)

Output matches:
{
  "metric": "sales",
  "column": "netweight_tmt",
  "unit": "MT" | "TMT",
  "sql_expression": "ROUND(SUM(...)::numeric, 2)",
  "alias": "actual_sales_mt" | "actual_sales_tmt"
}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional

from core.semantic_registry import get_registry


@dataclass
class ResolvedMetric:
    metric: str
    column: str
    unit: str
    sql_expression: str
    alias: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MetricResolver:
    """
    Programmatically resolves query metrics, columns, and unit conversions.
    """

    def __init__(self):
        self.registry = get_registry()

    def resolve(self, question: str, table_name: str = "industry_performance") -> ResolvedMetric:
        """
        Scan a query for metrics and units, returning the planned expression.

        Args:
            question: Natural language question.
            table_name: Context table to look up column mapping.

        Returns:
            ResolvedMetric details.
        """
        q_lower = question.lower()

        # ── 1. Detect Unit Request (MT/TMT) ───────────────────────────────────
        # Default is TMT. If "MT", "metric tonnes", "tonnes", "in tonnes", or "in mt" is found, switch to MT.
        unit = "TMT"
        # Match "mt", "metric tonnes", "tonnes" but avoid matching inside words
        if re.search(r"\b(mt|metric\s*tonnes|tonnes|tonne|metric\s*tonne)\b", q_lower):
            # Make sure it's not "tmt"
            if not re.search(r"\btmt\b", q_lower):
                unit = "MT"

        # ── 2. Detect Metric Concept ──────────────────────────────────────────
        # Default is "sales" (netweight_tmt).
        # Check if "target", "budget" or "plan" is requested
        metric_concept = "sales"
        if re.search(r"\b(target|budget|planned|plan|tgt)\b", q_lower) and table_name == "m60_level":
            metric_concept = "target"

        # ── 3. Resolve Column and SQL Expression from Semantic Registry ────────
        column = self.registry.resolve_column(metric_concept, table_name) or "netweight_tmt"
        
        # Look up pre-defined expressions in semantic registry
        sql_expression = self.registry.metric_expression(metric_concept, table_name, unit)
        alias = self.registry.metric_alias(metric_concept, table_name) or f"actual_{metric_concept}_{unit.lower()}"

        # If registry didn't define expression (e.g. fallback), construct it
        if not sql_expression:
            if unit == "MT":
                sql_expression = f"ROUND(SUM({column} * 1000)::numeric, 2)"
                alias = f"actual_sales_mt" if metric_concept == "sales" else f"target_mt"
            else:
                sql_expression = f"ROUND(SUM({column})::numeric, 2)"
                alias = f"actual_sales_tmt" if metric_concept == "sales" else f"target_tmt"

        # Correct alias suffix depending on unit
        if unit == "MT":
            alias = alias.replace("_tmt", "_mt")
        else:
            alias = alias.replace("_mt", "_tmt")

        return ResolvedMetric(
            metric=metric_concept,
            column=column,
            unit=unit,
            sql_expression=sql_expression,
            alias=alias
        )


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_metric_resolver() -> MetricResolver:
    return MetricResolver()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    resolver = MetricResolver()

    test_queries = [
        "Show sales for HPCL",                      # default TMT
        "Show actual sales of HSD in MT",           # MT conversion
        "What is the business volume in tonnes?",    # MT conversion
        "Show target quantity in TMT",              # Target metric (default)
        "What is the target in MT?",                # Target metric + MT conversion
    ]

    print("=== Metric Resolution Tests ===")
    for q in test_queries:
        print(f"\nQ: {q}")
        # Test on industry_performance
        res_ind = resolver.resolve(q, "industry_performance")
        print(f"  [industry_performance] Metric: {res_ind.metric:6s} | Unit: {res_ind.unit} | SQL: {res_ind.sql_expression} AS {res_ind.alias}")
        
        # Test on m60_level
        res_m60 = resolver.resolve(q, "m60_level")
        print(f"  [m60_level]            Metric: {res_m60.metric:6s} | Unit: {res_m60.unit} | SQL: {res_m60.sql_expression} AS {res_m60.alias}")
