"""
NLPSQL SYSTEM — QUERY PLANNER
==============================
Plans the logical query structure (table routing, dimensions, filters, ordering,
and formulas) without generating raw SQL.

Inputs:
  - Question String
  - Intent
  - Extracted Entities
  - Planned Filters
  - Metrics
  - Dimensions

Outputs:
  - Writes planned logical structure to metadata/query_plan.json
  - Returns structured query plan dictionary
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Any, Optional

from core.intent_detector import IntentResult
from core.entity_extractor import ExtractionResult
from core.semantic_registry import get_registry

_QUERY_PLAN_PATH = Path(__file__).resolve().parent.parent / "metadata" / "query_plan.json"


@dataclass
class QueryPlan:
    target_table: str
    intent: str
    select_metrics: List[Dict[str, str]]
    group_by: List[str]
    filters: List[Dict[str, Any]]
    order_by: List[str]
    limit: Optional[int] = None
    extra_options: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class QueryPlanner:
    """
    Plans queries programmatically using business metadata.
    """

    def __init__(self, output_path: Path = _QUERY_PLAN_PATH):
        self._output_path = output_path
        self.registry = get_registry()

    def plan(
        self,
        question: str,
        intent: IntentResult,
        entities: ExtractionResult,
        filters: List[Dict[str, Any]],
        metric: Dict[str, Any],
        dimensions: List[Dict[str, Any]],
        date_conditions: Optional[List[Dict[str, Any]]] = None
    ) -> QueryPlan:
        """
        Generates the query plan based on stage outputs.
        """
        # ── 1. Table Routing ──────────────────────────────────────────────────
        # Default is industry_performance
        target_table = "industry_performance"

        # If querying target quantities (from m60_level)
        if metric.get("metric") == "target":
            target_table = "m60_level"
        # If querying complex month-over-month growth calculations (often partitioned in mom_level)
        elif intent.primary_intent == "growth" and any(m in ["LPG", "RETAIL", "I&C", "LUBES"] for m in entities.sbus):
            target_table = "mom_level"

        # ── 2. Select metrics ─────────────────────────────────────────────────
        select_metrics = [{
            "expression": metric.get("sql_expression", ""),
            "alias": metric.get("alias", "")
        }]

        # ── 3. Group By columns ───────────────────────────────────────────────
        group_by_cols = []
        for d in dimensions:
            col = d.get("column")
            if col and col not in group_by_cols:
                group_by_cols.append(col)

        # ── 4. Combine Filters & Date Conditions ──────────────────────────────
        all_filters = list(filters)
        if date_conditions:
            for cond in date_conditions:
                all_filters.append({
                    "concept": "date_condition",
                    "column": cond.get("column"),
                    "operator": cond.get("operator"),
                    "values": cond.get("values"),
                    "type": "filter",
                    "description": cond.get("description")
                })

        # ── 5. Ordering & Limits ──────────────────────────────────────────────
        order_by = []
        limit_val = None

        # Sort metrics / dimensions based on intent
        metric_alias = metric.get("alias", "")
        if intent.primary_intent == "top_n":
            order_by.append(f"{metric_alias} DESC")
            # Default to 5 if top_n is detected without limit
            limit_val = 5
        elif intent.primary_intent == "bottom_n":
            order_by.append(f"{metric_alias} ASC")
            limit_val = 5

        # If a limit is explicitly mentioned in the query (e.g. top 5, bottom 10)
        m_limit = re.search(r"\b(?:top|bottom|first|last|rank)\s*(\d+)\b", question.lower())
        if m_limit:
            limit_val = int(m_limit.group(1))

        # ── 6. Extra options for special intents ──────────────────────────────
        extra = {}
        if intent.primary_intent == "growth":
            extra["requires_lag"] = True
            extra["order_column"] = "yearmonth"
            extra["partition_columns"] = group_by_cols or ["comname"]
        elif intent.primary_intent == "market_share":
            extra["requires_window_over"] = True
            # Check if a company is targeted
            if entities.companies:
                extra["company_target"] = entities.companies[0]

        plan_obj = QueryPlan(
            target_table=target_table,
            intent=intent.primary_intent,
            select_metrics=select_metrics,
            group_by=group_by_cols,
            filters=all_filters,
            order_by=order_by,
            limit=limit_val,
            extra_options=extra
        )

        # Write to file
        self._output_path.write_text(
            json.dumps(plan_obj.to_dict(), indent=2),
            encoding="utf-8"
        )

        return plan_obj


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_query_planner() -> QueryPlanner:
    return QueryPlanner()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from core.intent_detector import get_intent_detector
    from core.entity_extractor import get_entity_extractor
    from core.conditional_filter_planner import get_filter_planner
    from core.business_rule_engine import get_business_rule_engine
    from core.metric_resolver import get_metric_resolver
    from core.dimension_detector import get_dimension_detector
    from core.date_condition_planner import get_date_condition_planner

    planner = QueryPlanner()
    
    intent_det = get_intent_detector()
    extractor = get_entity_extractor()
    filter_plan = get_filter_planner()
    rule_eng = get_business_rule_engine()
    metric_res = get_metric_resolver()
    dim_det = get_dimension_detector()
    date_plan = get_date_condition_planner()

    test_queries = [
        "Top 5 districts by sales in FY 2024-2025",
        "Show sales monthly and company wise",
        "What is the difference in sales between HPCL and BPCL?",
    ]

    print("=== Query Planner Smoke Tests ===")
    for q in test_queries:
        print(f"\nQ: {q}")
        intent = intent_det.detect(q)
        entities = extractor.extract(q)
        metric = metric_res.resolve(q).to_dict()
        dims = [d.to_dict() for d in dim_det.detect(q)]
        
        # Date conditions
        date_conds = date_plan.plan(q)
        date_conds_dict = [c.to_dict() for c in date_conds] if date_conds else []

        # Filters
        plan_filters = filter_plan.plan(entities)["filters"]
        enforced = rule_eng.enforce("industry_performance", plan_filters)

        # Plan
        plan = planner.plan(
            question=q,
            intent=intent,
            entities=entities,
            filters=enforced,
            metric=metric,
            dimensions=dims,
            date_conditions=date_conds_dict
        )

        print("Generated Plan:")
        print(json.dumps(plan.to_dict(), indent=2))
