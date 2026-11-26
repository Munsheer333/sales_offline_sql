"""
NLPSQL SYSTEM - PHASE 06B
Enterprise Query Pattern Engine

This module upgrades the gold-query shortcut into reusable, deterministic query
patterns. It does not replace vector retrieval or query history. It can render
safe PostgreSQL templates for high-confidence business query shapes, fingerprint
queries for clustering/cache keys, and optionally use a migration-safe pattern
result cache.
"""

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import psycopg2
from core.db_pool import pooled_connect
import sqlglot
from sqlglot import exp

from config import CURRENT_FINANCIAL_YEAR
from core.business_formula_engine import FORMULA_ENGINE
from core.phase5_router import (
    COMPANY_NAMES,
    extract_lightweight_filters,
    identify_metrics,
    infer_routing_dimensions,
    parse_time_ranges,
    route_question,
)


PATTERN_ENGINE_VERSION = "query-pattern-engine-v1"
DEFAULT_PATTERN_CONFIDENCE = 0.84
DEFAULT_CACHE_TTL_SECONDS = 300


@dataclass(frozen=True)
class QueryTemplate:
    template_id: str
    name: str
    complexity: str
    metric_name: str
    required_slots: Tuple[str, ...]
    optional_slots: Tuple[str, ...] = tuple()


QUERY_TEMPLATES: Tuple[QueryTemplate, ...] = (
    QueryTemplate(
        template_id="industry_market_share_rank_v1",
        name="Industry market share ranking",
        complexity="HARD",
        metric_name="market_share_percentage",
        required_slots=("fiscal_year",),
        optional_slots=("order_direction", "limit", "company", "state", "product", "dimensions"),
    ),
    QueryTemplate(
        template_id="industry_sales_growth_comparison_v1",
        name="Industry sales growth comparison",
        complexity="HARD",
        metric_name="actual_sales",
        required_slots=("fiscal_year_start", "fiscal_year_end"),
        optional_slots=("company", "sbu", "product", "state", "district", "zone"),
    ),
    QueryTemplate(
        template_id="mom_product_growth_threshold_v1",
        name="MOM product sales growth threshold",
        complexity="HARD",
        metric_name="actual_sales",
        required_slots=("fiscal_year_start", "fiscal_year_end", "growth_threshold"),
        optional_slots=("growth_operator",),
    ),
    QueryTemplate(
        template_id="mom_actual_sales_sum_v1",
        name="MOM actual sales total",
        complexity="MEDIUM",
        metric_name="actual_sales",
        required_slots=tuple(),
        optional_slots=("sbu", "product", "date_range", "fiscal_year", "month", "unit"),
    ),
    QueryTemplate(
        template_id="industry_actual_sales_sum_v1",
        name="Industry company actual sales total",
        complexity="MEDIUM",
        metric_name="actual_sales",
        required_slots=("company",),
        optional_slots=("sbu", "product", "fiscal_year", "month", "state", "district", "zone", "unit"),
    ),
    QueryTemplate(
        template_id="m60_target_actual_variance_v1",
        name="M60 target versus actual variance",
        complexity="HARD",
        metric_name="target_quantity",
        required_slots=tuple(),
        optional_slots=("sbu", "zone", "product", "fiscal_year", "month", "dimensions"),
    ),
    QueryTemplate(
        template_id="m60_target_quantity_sum_v1",
        name="M60 target quantity total",
        complexity="MEDIUM",
        metric_name="target_quantity",
        required_slots=tuple(),
        optional_slots=("sbu", "zone", "product", "fiscal_year", "month", "unit"),
    ),
    QueryTemplate(
        template_id="industry_monthly_trend_v1",
        name="Industry monthly sales trend",
        complexity="MEDIUM",
        metric_name="actual_sales",
        required_slots=("fiscal_year",),
        optional_slots=("company", "sbu", "product", "state", "district", "zone", "region", "unit"),
    ),
    QueryTemplate(
        template_id="mom_monthly_trend_v1",
        name="MOM monthly sales trend",
        complexity="MEDIUM",
        metric_name="actual_sales",
        required_slots=("fiscal_year",),
        optional_slots=("sbu", "product", "state", "district", "zone", "region", "unit"),
    ),
    QueryTemplate(
        template_id="industry_quarterly_trend_v1",
        name="Industry quarterly sales trend",
        complexity="MEDIUM",
        metric_name="actual_sales",
        required_slots=("fiscal_year",),
        optional_slots=("company", "sbu", "product", "state", "district", "zone", "region", "unit"),
    ),
    QueryTemplate(
        template_id="mom_quarterly_trend_v1",
        name="MOM quarterly sales trend",
        complexity="MEDIUM",
        metric_name="actual_sales",
        required_slots=("fiscal_year",),
        optional_slots=("sbu", "product", "state", "district", "zone", "region", "unit"),
    ),
    QueryTemplate(
        template_id="industry_yearly_trend_v1",
        name="Industry yearly sales trend",
        complexity="MEDIUM",
        metric_name="actual_sales",
        required_slots=tuple(),
        optional_slots=("company", "sbu", "product", "state", "district", "zone", "region", "unit"),
    ),
    QueryTemplate(
        template_id="mom_yearly_trend_v1",
        name="MOM yearly sales trend",
        complexity="MEDIUM",
        metric_name="actual_sales",
        required_slots=tuple(),
        optional_slots=("sbu", "product", "state", "district", "zone", "region", "unit"),
    ),
    QueryTemplate(
        template_id="industry_highest_lowest_month_v1",
        name="Industry highest/lowest month sales",
        complexity="MEDIUM",
        metric_name="actual_sales",
        required_slots=("fiscal_year", "order_direction", "limit"),
        optional_slots=("company", "sbu", "product", "state", "district", "zone", "region", "unit"),
    ),
    QueryTemplate(
        template_id="mom_highest_lowest_month_v1",
        name="MOM highest/lowest month sales",
        complexity="MEDIUM",
        metric_name="actual_sales",
        required_slots=("fiscal_year", "order_direction", "limit"),
        optional_slots=("sbu", "product", "state", "district", "zone", "region", "unit"),
    ),
    QueryTemplate(
        template_id="industry_mom_growth_v1",
        name="Industry MoM sales growth",
        complexity="HARD",
        metric_name="actual_sales",
        required_slots=("fiscal_year",),
        optional_slots=("company", "sbu", "product", "state", "district", "zone", "region", "unit"),
    ),
    QueryTemplate(
        template_id="mom_mom_growth_v1",
        name="MOM MoM sales growth",
        complexity="HARD",
        metric_name="actual_sales",
        required_slots=("fiscal_year",),
        optional_slots=("sbu", "product", "state", "district", "zone", "region", "unit"),
    ),
    QueryTemplate(
        template_id="industry_declining_states_v1",
        name="Industry declining/growing states by YoY sales",
        complexity="HARD",
        metric_name="actual_sales",
        required_slots=tuple(),
        optional_slots=("company", "sbu", "product", "direction", "fiscal_year_start", "fiscal_year_end"),
    ),
    QueryTemplate(
        template_id="mom_product_best_growth_v1",
        name="MOM product with highest/lowest MOM growth",
        complexity="HARD",
        metric_name="actual_sales",
        required_slots=tuple(),
        optional_slots=("sbu", "fiscal_year", "order_direction", "limit", "unit"),
    ),
)


def match_query_pattern(
    question: str,
    schema_metadata: Optional[dict] = None,
    similar_examples: Optional[Sequence[dict]] = None,
    min_confidence: float = DEFAULT_PATTERN_CONFIDENCE,
    entity_matches: Optional[Sequence[dict]] = None,
) -> Dict[str, Any]:
    """Return a rendered query pattern when the question fits a known template."""
    routing = route_question(question, schema_metadata=schema_metadata)
    slots = extract_query_slots(question, routing, entity_matches=entity_matches)
    clusters = cluster_similar_examples(similar_examples or [])

    candidates = []
    for template in QUERY_TEMPLATES:
        score, reasons = _score_template(template, question, routing, slots, clusters)
        if not _template_slots_available(template, slots):
            reasons.append("missing_required_slots")
            score = min(score, 0.45)
        candidates.append((score, template, reasons))

    candidates.sort(key=lambda item: (item[0], item[1].template_id), reverse=True)
    best_score, template, reasons = candidates[0]
    if best_score < min_confidence:
        return {
            "matched": False,
            "engine_version": PATTERN_ENGINE_VERSION,
            "confidence": round(best_score, 4),
            "template_id": template.template_id,
            "reason": "No reusable query pattern passed the confidence threshold.",
            "slots": _public_slots(slots),
            "clusters": clusters,
            "routing": routing,
        }

    rendered = render_query_template(template, slots)
    execution_fingerprint = execution_cache_fingerprint(rendered["parameterized_sql"], rendered["params"])

    return {
        "matched": True,
        "engine_version": PATTERN_ENGINE_VERSION,
        "question": question,
        "template_id": template.template_id,
        "template_name": template.name,
        "complexity": template.complexity,
        "confidence": round(best_score, 4),
        "reasons": reasons,
        "sql": rendered["sql"],
        "parameterized_sql": rendered["parameterized_sql"],
        "params": rendered["params"],
        "slots": _public_slots(slots),
        "query_fingerprint": question_fingerprint(question),
        "sql_fingerprint": sql_fingerprint(rendered["parameterized_sql"], include_literals=False),
        "execution_fingerprint": execution_fingerprint,
        "clusters": clusters,
        "routing": routing,
        "cache": {
            "enabled": True,
            "ttl_seconds": DEFAULT_CACHE_TTL_SECONDS,
            "execution_fingerprint": execution_fingerprint,
        },
    }


async def match_query_pattern_async(
    question: str,
    schema_metadata: Optional[dict] = None,
    similar_examples: Optional[Sequence[dict]] = None,
    min_confidence: float = DEFAULT_PATTERN_CONFIDENCE,
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        match_query_pattern,
        question,
        schema_metadata,
        similar_examples,
        min_confidence,
    )

def _extract_explicit_sbu(q_norm: str, original_question: str) -> Optional[str]:
    if "target" not in q_norm and "m60" not in q_norm:
        return None
    match = re.search(
        r"\b(?:strategic\s+business\s+unit|sbu)\s+(HPCL\s+Mkt\s+[a-zA-Z0-9&/ \-]+?)(?=\s+(?:in|for|from|during|between|and|fy|fiscal|of)\b|$)",
        original_question,
        flags=re.IGNORECASE
    )
    if match:
        return match.group(1).strip()
    return None


def extract_query_slots(
    question: str,
    routing: Optional[dict] = None,
    entity_matches: Optional[Sequence[dict]] = None,
) -> Dict[str, dict]:
    routing = routing or route_question(question)
    filters = routing.get("filters") or extract_lightweight_filters(question)
    time_ranges = routing.get("time_ranges") or parse_time_ranges(question)
    metrics = routing.get("metrics") or identify_metrics(question)
    dimensions = routing.get("dimensions") or infer_routing_dimensions(question)
    q_norm = _normalize(question)

    slots: Dict[str, dict] = {}

    explicit_sbu = _extract_explicit_sbu(q_norm, question)
    if explicit_sbu:
        _set_slot(slots, "sbu", explicit_sbu, "explicit_label", 0.98)

    for metric in metrics:
        _set_slot(slots, "metric", metric.get("name"), "metric", metric.get("confidence", 0.8))

    for item in filters:
        filter_type = item.get("type")
        if filter_type in {"company", "product", "sbu", "state", "zone", "district"}:
            if filter_type in {"company", "product"} and explicit_sbu and item.get("value", "").lower() in explicit_sbu.lower():
                continue
            if filter_type == "sbu" and explicit_sbu:
                continue
            _set_slot(slots, filter_type, item.get("value"), filter_type, 0.86)

    for item in entity_matches or []:
        entity_type = item.get("type")
        if entity_type in {"company", "product", "sbu", "state", "zone", "district"}:
            if entity_type in {"company", "product"} and explicit_sbu and item.get("value", "").lower() in explicit_sbu.lower():
                continue
            if entity_type == "sbu" and explicit_sbu:
                continue
            _set_slot(slots, entity_type, item.get("value"), "database_entity_catalog", 0.98)

    product = _slot_value(slots, "product")
    sbu = _slot_value(slots, "sbu")
    zone = _slot_value(slots, "zone")
    if isinstance(zone, str):
        for slot_name, value in [("product", product), ("sbu", sbu)]:
            if (
                isinstance(value, str)
                and zone.casefold() != value.casefold()
                and re.search(rf"\b{re.escape(value.casefold())}\b", zone.casefold())
                and not _labeled_context(q_norm, value.casefold(), slot_name)
            ):
                slots.pop(slot_name, None)
        product = _slot_value(slots, "product")
        sbu = _slot_value(slots, "sbu")
    if (
        isinstance(product, str)
        and isinstance(sbu, str)
        and product.casefold() != sbu.casefold()
        and re.search(rf"\b{re.escape(sbu.casefold())}\b", product.casefold())
        and not _labeled_context(q_norm, sbu.casefold(), "sbu")
    ):
        slots.pop("sbu", None)

    for item in time_ranges:
        if item.get("type") == "fiscal_year":
            _set_slot(slots, "fiscal_year", item.get("value"), "time_range", 0.90, dynamic=item.get("dynamic", False))
        elif item.get("type") == "fiscal_year_range":
            years = sorted(item.get("value") or [], key=_fiscal_year_start)
            if len(years) >= 2:
                _set_slot(slots, "fiscal_year_start", years[0], "time_range", 0.92)
                _set_slot(slots, "fiscal_year_end", years[1], "time_range", 0.92)
        elif item.get("type") == "date_range":
            _set_slot(slots, "date_range", item.get("value"), "time_range", 0.88)
        elif item.get("type") == "month":
            _set_slot(slots, "month", item.get("value"), "time_range", 0.84, dynamic=item.get("dynamic", False))

    if re.search(r"\bmt\b", q_norm) and not re.search(r"\btmt\b", q_norm):
        _set_slot(slots, "unit", "MT", "unit", 0.86)
    elif re.search(r"\bkl\b", q_norm):
        _set_slot(slots, "unit", "KL", "unit", 0.86)
    else:
        _set_slot(slots, "unit", "TMT", "unit", 0.72)

    if any(term in q_norm for term in ["lowest", "least", "bottom", "minimum", "min"]):
        _set_slot(slots, "order_direction", "ASC", "ordering", 0.88)
    elif any(term in q_norm for term in ["highest", "largest", "maximum", "max", "top", "rank", "ranking"]):
        _set_slot(slots, "order_direction", "DESC", "ordering", 0.88)

    limit_match = re.search(r"\b(?:top|bottom|first|last)\s+(\d{1,3})\b", q_norm)
    if limit_match:
        _set_slot(slots, "limit", min(int(limit_match.group(1)), 500), "limit", 0.88)
    elif "lowest" in q_norm or "highest" in q_norm:
        _set_slot(slots, "limit", 1, "limit", 0.82)

    if dimensions:
        _set_slot(slots, "dimensions", [item.get("source") for item in dimensions], "dimension", 0.76)
    growth_threshold = _extract_growth_threshold(q_norm)
    if growth_threshold:
        _set_slot(slots, "growth_operator", growth_threshold[0], "threshold", 0.94)
        _set_slot(slots, "growth_threshold", growth_threshold[1], "threshold", 0.94)
    if _has_target_actual_comparison_intent(q_norm):
        _set_slot(slots, "actual_comparison", True, "comparison", 0.94)
        _set_slot(
            slots,
            "comparison_aggregation",
            "AVG" if any(term in q_norm for term in ["average", "avg", "mean"]) else "SUM",
            "comparison",
            0.90,
        )

    # Determine trend_type
    trend_type = None
    if any(term in q_norm for term in ["monthly", "month over month", "month-over-month", "month by month", "mom"]):
        trend_type = "monthly"
    elif any(term in q_norm for term in ["quarterly", "quarter over quarter", "quarter-over-quarter", "quarter by quarter", "qoq"]):
        trend_type = "quarterly"
    elif any(term in q_norm for term in ["yearly", "year over year", "year-on-year", "yoy"]):
        trend_type = "yearly"
    else:
        has_trend_signal = any(term in q_norm for term in ["trend", "highest", "lowest", "best", "worst", "max", "min", "growth", "difference", "compare", "comparison", "versus", "vs", "against"])
        if has_trend_signal:
            if "month" in q_norm:
                trend_type = "monthly"
            elif "quarter" in q_norm:
                trend_type = "quarterly"
            elif "year" in q_norm:
                trend_type = "yearly"

    if trend_type:
        _set_slot(slots, "trend_type", trend_type, "trend", 0.95)

    # Determine trend_metric
    trend_metric = None
    if any(term in q_norm for term in ["growth", "difference", "percentage"]):
        trend_metric = "growth"
    elif any(term in q_norm for term in ["highest", "lowest", "best", "worst", "max", "min"]):
        trend_metric = "highest_lowest"

    if trend_metric:
        _set_slot(slots, "trend_metric", trend_metric, "trend", 0.95)

    if trend_metric == "highest_lowest":
        if any(term in q_norm for term in ["lowest", "worst", "min", "least"]):
            _set_slot(slots, "order_direction", "ASC", "ordering", 0.88)
        else:
            _set_slot(slots, "order_direction", "DESC", "ordering", 0.88)
        _set_slot(slots, "limit", 1, "limit", 0.82)

    if "fiscal_year" not in slots and "fiscal_year_start" not in slots and "fiscal_year_end" not in slots:
        if trend_type in {"monthly", "quarterly"} or any(term in q_norm for term in ["monthly", "quarterly", "month", "quarter"]):
            _set_slot(slots, "fiscal_year", CURRENT_FINANCIAL_YEAR, "default_trend_time_range", 0.90)

    return slots


def _render_declining_states_template(slots: Dict[str, dict]) -> Dict[str, Any]:
    """Year-over-year state comparison to find declining or growing states.

    Uses CURRENT_FINANCIAL_YEAR vs previous year by default. Accepts explicit
    fiscal_year_start / fiscal_year_end slots to override.
    """
    from config import CURRENT_FINANCIAL_YEAR as _CFY
    _start = int(_CFY.split("-")[0])
    default_cy = _CFY
    default_ly = f"{_start - 1}-{_start}"

    cy = _slot_value(slots, "fiscal_year_end") or _slot_value(slots, "fiscal_year") or default_cy
    ly = _slot_value(slots, "fiscal_year_start") or default_ly

    params: List[Any] = [ly, cy, ly, cy]

    # optional filters
    extra_raw: List[str] = []
    extra_param: List[str] = []
    company_val = _slot_value(slots, "company")
    sbu_val = _slot_value(slots, "sbu")
    if company_val:
        extra_raw.append(f"industry_performance.comname = {_literal(company_val)}")
        extra_param.append("industry_performance.comname = %s")
        params.insert(0, company_val)
        params.insert(2, company_val)
    if sbu_val:
        extra_raw.append(f"industry_performance.sbu_name = {_literal(sbu_val.upper())}")
        extra_param.append("industry_performance.sbu_name = %s")
        params.insert(0, sbu_val.upper())
        params.insert(2, sbu_val.upper())

    extra_raw_sql = (" AND " + " AND ".join(extra_raw)) if extra_raw else ""
    extra_param_sql = (" AND " + " AND ".join(extra_param)) if extra_param else ""

    # Detect direction: default to declining (<)
    q_norm = _normalize(" ".join(
        str(s.get("value", "")) for s in slots.values() if isinstance(s, dict)
    ))
    having_op = "<"  # declining
    order_dir = "ASC"
    if any(term in q_norm for term in ["growing", "improved", "increasing", "growth"]):
        having_op = ">"
        order_dir = "DESC"

    sql = f"""WITH state_sales AS (
  SELECT statename,
         SUM(CASE WHEN fiscal_year = {_literal(ly)} THEN netweight_tmt ELSE 0 END) AS previous_sales,
         SUM(CASE WHEN fiscal_year = {_literal(cy)} THEN netweight_tmt ELSE 0 END) AS current_sales
  FROM industry_performance
  WHERE (category != 'O' OR category IS NULL)
    AND fiscal_year IN ({_literal(ly)}, {_literal(cy)}){extra_raw_sql}
  GROUP BY statename
)
SELECT statename,
       ROUND(previous_sales::numeric, 2) AS previous_sales_tmt,
       ROUND(current_sales::numeric, 2) AS current_sales_tmt,
       ROUND(((current_sales - previous_sales) * 100.0) / NULLIF(previous_sales, 0), 2) AS growth_percentage
FROM state_sales
WHERE current_sales {having_op} previous_sales
ORDER BY growth_percentage {order_dir};"""

    parameterized_sql = f"""WITH state_sales AS (
  SELECT statename,
         SUM(CASE WHEN fiscal_year = %s THEN netweight_tmt ELSE 0 END) AS previous_sales,
         SUM(CASE WHEN fiscal_year = %s THEN netweight_tmt ELSE 0 END) AS current_sales
  FROM industry_performance
  WHERE (category != 'O' OR category IS NULL)
    AND fiscal_year IN (%s, %s){extra_param_sql}
  GROUP BY statename
)
SELECT statename,
       ROUND(previous_sales::numeric, 2) AS previous_sales_tmt,
       ROUND(current_sales::numeric, 2) AS current_sales_tmt,
       ROUND(((current_sales - previous_sales) * 100.0) / NULLIF(previous_sales, 0), 2) AS growth_percentage
FROM state_sales
WHERE current_sales {having_op} previous_sales
ORDER BY growth_percentage {order_dir};"""

    return {"sql": sql, "parameterized_sql": parameterized_sql, "params": params}


def _render_product_best_growth_template(slots: Dict[str, dict]) -> Dict[str, Any]:
    """Find the product(s) with the highest/lowest average month-over-month growth rate.

    Strategy:
    1. CTE monthly_product_sales: SUM sales by (productname, month_name, yearmonth)
    2. CTE product_mom: compute LAG-based MOM growth per product per month
    3. Final SELECT: AVG(mom_growth_percentage) per product, ORDER BY that, LIMIT N
    """
    fy = _slot_value(slots, "fiscal_year") or CURRENT_FINANCIAL_YEAR
    unit = _enum_slot(slots, "unit", {"TMT", "MT"}, default="TMT")
    expression = "mom_level.netweight_tmt" if unit == "TMT" else "mom_level.netweight_tmt * 1000"
    sales_alias = "sales_tmt" if unit == "TMT" else "sales_mt"
    direction = _enum_slot(slots, "order_direction", {"ASC", "DESC"}, default="DESC")
    limit = _int_slot(slots, "limit", default=1, low=1, high=500)

    extra_raw: List[str] = []
    extra_param: List[str] = []
    params: List[Any] = []
    sbu_val = _slot_value(slots, "sbu")
    if sbu_val:
        extra_raw.append(f"mom_level.sbu_name = {_literal(sbu_val)}")
        extra_param.append("mom_level.sbu_name = %s")
        params.append(sbu_val)

    params.append(fy)
    extra_raw_sql = (" AND " + " AND ".join(extra_raw)) if extra_raw else ""
    extra_param_sql = (" AND " + " AND ".join(extra_param)) if extra_param else ""

    sql = f"""WITH monthly_product_sales AS (
  SELECT productname, month_name, yearmonth,
         SUM({expression}) AS {sales_alias}
  FROM mom_level
  WHERE mom_level.sbu_name != '0'
    AND mom_level.sbu_name NOT IN ('Common', 'Mumbai Ref', 'Renewable Energy', 'Visakh Ref')
    AND mom_level.fiscal_year = {_literal(fy)}{extra_raw_sql}
  GROUP BY productname, month_name, yearmonth
),
product_mom AS (
  SELECT productname, month_name, yearmonth, {sales_alias},
         ROUND((({sales_alias} - LAG({sales_alias}) OVER (PARTITION BY productname ORDER BY yearmonth)) * 100.0)
               / NULLIF(LAG({sales_alias}) OVER (PARTITION BY productname ORDER BY yearmonth), 0), 2) AS mom_growth_pct
  FROM monthly_product_sales
)
SELECT productname,
       ROUND(AVG(mom_growth_pct)::numeric, 2) AS avg_mom_growth_percentage
FROM product_mom
WHERE mom_growth_pct IS NOT NULL
GROUP BY productname
ORDER BY avg_mom_growth_percentage {direction}
LIMIT {limit};"""

    parameterized_sql = f"""WITH monthly_product_sales AS (
  SELECT productname, month_name, yearmonth,
         SUM({expression}) AS {sales_alias}
  FROM mom_level
  WHERE mom_level.sbu_name != '0'
    AND mom_level.sbu_name NOT IN ('Common', 'Mumbai Ref', 'Renewable Energy', 'Visakh Ref')
    AND mom_level.fiscal_year = %s{extra_param_sql}
  GROUP BY productname, month_name, yearmonth
),
product_mom AS (
  SELECT productname, month_name, yearmonth, {sales_alias},
         ROUND((({sales_alias} - LAG({sales_alias}) OVER (PARTITION BY productname ORDER BY yearmonth)) * 100.0)
               / NULLIF(LAG({sales_alias}) OVER (PARTITION BY productname ORDER BY yearmonth), 0), 2) AS mom_growth_pct
  FROM monthly_product_sales
)
SELECT productname,
       ROUND(AVG(mom_growth_pct)::numeric, 2) AS avg_mom_growth_percentage
FROM product_mom
WHERE mom_growth_pct IS NOT NULL
GROUP BY productname
ORDER BY avg_mom_growth_percentage {direction}
LIMIT {limit};"""

    return {"sql": sql, "parameterized_sql": parameterized_sql, "params": params}


def render_query_template(template: QueryTemplate, slots: Dict[str, dict]) -> Dict[str, Any]:
    if template.template_id == "industry_market_share_rank_v1":
        return _render_market_share_template(slots)
    if template.template_id == "industry_sales_growth_comparison_v1":
        return _render_sales_growth_comparison_template(slots)
    if template.template_id == "mom_product_growth_threshold_v1":
        return _render_mom_product_growth_threshold_template(slots)
    if template.template_id == "mom_actual_sales_sum_v1":
        return _render_actual_sales_template(slots)
    if template.template_id == "industry_actual_sales_sum_v1":
        return _render_industry_actual_sales_template(slots)
    if template.template_id == "m60_target_quantity_sum_v1":
        return _render_target_quantity_template(slots)
    if template.template_id == "m60_target_actual_variance_v1":
        return _render_target_actual_variance_template(slots)
    if template.template_id == "industry_monthly_trend_v1":
        return _render_monthly_trend_template(slots, "industry_performance")
    if template.template_id == "mom_monthly_trend_v1":
        return _render_monthly_trend_template(slots, "mom_level")
    if template.template_id == "industry_quarterly_trend_v1":
        return _render_quarterly_trend_template(slots, "industry_performance")
    if template.template_id == "mom_quarterly_trend_v1":
        return _render_quarterly_trend_template(slots, "mom_level")
    if template.template_id == "industry_yearly_trend_v1":
        return _render_yearly_trend_template(slots, "industry_performance")
    if template.template_id == "mom_yearly_trend_v1":
        return _render_yearly_trend_template(slots, "mom_level")
    if template.template_id == "industry_highest_lowest_month_v1":
        return _render_highest_lowest_month_template(slots, "industry_performance")
    if template.template_id == "mom_highest_lowest_month_v1":
        return _render_highest_lowest_month_template(slots, "mom_level")
    if template.template_id == "industry_mom_growth_v1":
        return _render_mom_growth_template(slots, "industry_performance")
    if template.template_id == "mom_mom_growth_v1":
        return _render_mom_growth_template(slots, "mom_level")
    if template.template_id == "industry_declining_states_v1":
        return _render_declining_states_template(slots)
    if template.template_id == "mom_product_best_growth_v1":
        return _render_product_best_growth_template(slots)
    raise ValueError(f"Unknown query template: {template.template_id}")


def cluster_similar_examples(similar_examples: Sequence[dict]) -> List[Dict[str, Any]]:
    clusters: Dict[str, dict] = {}
    for example in similar_examples:
        sql = example.get("sql") or example.get("gold_sql") or ""
        if not sql:
            continue
        key = sql_fingerprint(sql, include_literals=False)
        cluster = clusters.setdefault(key, {
            "cluster_id": key,
            "sql_fingerprint": key,
            "size": 0,
            "best_similarity": 0.0,
            "representative_question": example.get("question", ""),
            "representative_sql": sql,
            "complexities": set(),
            "example_ids": [],
        })
        cluster["size"] += 1
        cluster["best_similarity"] = max(cluster["best_similarity"], _example_similarity(example))
        if example.get("complexity"):
            cluster["complexities"].add(example.get("complexity"))
        if example.get("id") is not None:
            cluster["example_ids"].append(example.get("id"))

    public = []
    for cluster in clusters.values():
        public.append({
            "cluster_id": cluster["cluster_id"],
            "sql_fingerprint": cluster["sql_fingerprint"],
            "size": cluster["size"],
            "best_similarity": round(cluster["best_similarity"], 6),
            "representative_question": cluster["representative_question"],
            "representative_sql": cluster["representative_sql"],
            "complexities": sorted(cluster["complexities"]),
            "example_ids": cluster["example_ids"],
        })
    public.sort(key=lambda item: (item["best_similarity"], item["size"], item["cluster_id"]), reverse=True)
    return public


def question_fingerprint(question: str) -> str:
    normalized = _normalize(question)
    normalized = re.sub(r"\b20\d{2}\s*[-/]\s*20\d{2}\b", "<fiscal_year>", normalized)
    normalized = re.sub(
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+20\d{2}\b",
        "<date>",
        normalized,
    )
    normalized = re.sub(r"\b\d{1,3}\b", "<number>", normalized)
    for token in [*(name.lower() for name in COMPANY_NAMES), "hsd", "ms", "lpg", "retail"]:
        normalized = re.sub(rf"\b{re.escape(token)}\b", f"<{token}_slot>", normalized)
    return _hash_text(f"question:{normalized}")


def sql_fingerprint(sql: str, include_literals: bool = False) -> str:
    normalized = normalize_sql_for_fingerprint(sql, include_literals=include_literals)
    return _hash_text(f"sql:{normalized}")


def execution_cache_fingerprint(parameterized_sql: str, params: Sequence[Any]) -> str:
    payload = {
        "sql": normalize_sql_for_fingerprint(parameterized_sql, include_literals=True),
        "params": list(params),
    }
    return _hash_text(json.dumps(payload, sort_keys=True, default=str))


def normalize_sql_for_fingerprint(sql: str, include_literals: bool = False) -> str:
    cleaned = (sql or "").strip().rstrip(";")
    try:
        parsed = sqlglot.parse_one(cleaned, read="postgres")
        cleaned = parsed.sql(dialect="postgres", normalize=True)
    except Exception:
        cleaned = cleaned.lower()

    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    if include_literals:
        return cleaned
    cleaned = re.sub(r"'(?:''|[^'])*'", "?", cleaned)
    cleaned = re.sub(r"\b\d+(?:\.\d+)?\b", "?", cleaned)
    cleaned = re.sub(r"\$\d+|%s", "?", cleaned)
    return cleaned


def create_query_pattern_cache_tables(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS nlpsql_query_pattern_cache (
            execution_fingerprint TEXT PRIMARY KEY,
            pattern_id TEXT NOT NULL,
            question_fingerprint TEXT NOT NULL,
            sql_fingerprint TEXT NOT NULL,
            question TEXT NOT NULL,
            sql TEXT NOT NULL,
            parameterized_sql TEXT,
            params_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            columns_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            rows_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            row_count INTEGER NOT NULL DEFAULT 0,
            execution_time_ms INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            hit_count INTEGER NOT NULL DEFAULT 0,
            verification_status TEXT NOT NULL DEFAULT 'candidate'
        );
    """)
    cur.execute("""
        ALTER TABLE nlpsql_query_pattern_cache
        ADD COLUMN IF NOT EXISTS verification_status TEXT NOT NULL DEFAULT 'candidate';
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_nlpsql_pattern_cache_pattern
        ON nlpsql_query_pattern_cache (pattern_id, updated_at DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_nlpsql_pattern_cache_question
        ON nlpsql_query_pattern_cache (question_fingerprint);
    """)


def ensure_query_pattern_cache(conn_str: str) -> bool:
    try:
        conn = pooled_connect(conn_str)
        cur = conn.cursor()
        _ensure_pattern_tables_once(conn, cur, conn_str)
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception:
        return False


_ENSURED_PATTERN_CONNS: set = set()


def _ensure_pattern_tables_once(conn, cur, conn_str: str) -> None:
    if conn_str in _ENSURED_PATTERN_CONNS:
        return
    create_query_pattern_cache_tables(cur)
    conn.commit()
    _ENSURED_PATTERN_CONNS.add(conn_str)


def get_cached_pattern_result(
    conn_str: str,
    execution_fingerprint: str,
    max_age_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> Optional[dict]:
    try:
        conn = pooled_connect(conn_str)
        cur = conn.cursor()
        _ensure_pattern_tables_once(conn, cur, conn_str)
        cur.execute(
            """
            SELECT pattern_id, question, sql, parameterized_sql, params_json,
                   columns_json, rows_json, row_count, execution_time_ms, hit_count
            FROM nlpsql_query_pattern_cache
            WHERE execution_fingerprint = %s
              AND updated_at >= NOW() - (%s * INTERVAL '1 second')
              AND verification_status = 'verified'
            """,
            (execution_fingerprint, int(max_age_seconds)),
        )
        row = cur.fetchone()
        if not row:
            conn.commit()
            cur.close()
            conn.close()
            return None

        cur.execute(
            """
            UPDATE nlpsql_query_pattern_cache
            SET hit_count = hit_count + 1, updated_at = NOW()
            WHERE execution_fingerprint = %s
            """,
            (execution_fingerprint,),
        )
        conn.commit()
        cur.close()
        conn.close()

        return {
            "success": True,
            "pattern_id": row[0],
            "question": row[1],
            "sql": row[2],
            "parameterized_sql": row[3],
            "sql_params": _json_value(row[4], []),
            "columns": _json_value(row[5], []),
            "rows": _json_value(row[6], []),
            "row_count": row[7],
            "execution_time_ms": row[8],
            "cache_hit": True,
            "cache_hit_count": row[9] + 1,
            "error": None,
            "warnings": [],
            "healed": False,
        }
    except Exception:
        return None


def store_pattern_result(
    conn_str: str,
    pattern_match: dict,
    execution_result: dict,
) -> bool:
    if not execution_result.get("success"):
        return False
    try:
        conn = pooled_connect(conn_str)
        cur = conn.cursor()
        _ensure_pattern_tables_once(conn, cur, conn_str)
        cur.execute(
            """
            INSERT INTO nlpsql_query_pattern_cache (
                execution_fingerprint, pattern_id, question_fingerprint, sql_fingerprint,
                question, sql, parameterized_sql, params_json, columns_json, rows_json,
                row_count, execution_time_ms, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, NOW())
            ON CONFLICT (execution_fingerprint) DO UPDATE SET
                sql = EXCLUDED.sql,
                parameterized_sql = EXCLUDED.parameterized_sql,
                params_json = EXCLUDED.params_json,
                columns_json = EXCLUDED.columns_json,
                rows_json = EXCLUDED.rows_json,
                row_count = EXCLUDED.row_count,
                execution_time_ms = EXCLUDED.execution_time_ms,
                updated_at = NOW()
            """,
            (
                pattern_match["execution_fingerprint"],
                pattern_match["template_id"],
                pattern_match["query_fingerprint"],
                pattern_match["sql_fingerprint"],
                pattern_match.get("question") or "",
                execution_result.get("sql") or pattern_match["sql"],
                pattern_match.get("parameterized_sql"),
                json.dumps(pattern_match.get("params", []), default=str),
                json.dumps(execution_result.get("columns", []), default=str),
                json.dumps(execution_result.get("rows", []), default=str),
                int(execution_result.get("row_count", 0) or 0),
                int(execution_result.get("execution_time_ms", 0) or 0),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception:
        return False


def reject_cached_pattern_result(conn_str: str, execution_fingerprint: str) -> bool:
    try:
        conn = pooled_connect(conn_str)
        cur = conn.cursor()
        _ensure_pattern_tables_once(conn, cur, conn_str)
        cur.execute(
            """
            UPDATE nlpsql_query_pattern_cache
            SET verification_status = 'rejected', updated_at = NOW()
            WHERE execution_fingerprint = %s
            """,
            (execution_fingerprint,),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception:
        return False


async def get_cached_pattern_result_async(
    conn_str: str,
    execution_fingerprint: str,
    max_age_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> Optional[dict]:
    return await asyncio.to_thread(get_cached_pattern_result, conn_str, execution_fingerprint, max_age_seconds)


async def store_pattern_result_async(conn_str: str, pattern_match: dict, execution_result: dict) -> bool:
    return await asyncio.to_thread(store_pattern_result, conn_str, pattern_match, execution_result)


def summarize_pattern_match(pattern_match: Optional[dict], cache_hit: bool = False, cache_stored: bool = False) -> dict:
    if not pattern_match or not pattern_match.get("matched"):
        return {"matched": False}
    return {
        "matched": True,
        "template_id": pattern_match.get("template_id"),
        "template_name": pattern_match.get("template_name"),
        "confidence": pattern_match.get("confidence"),
        "slots": pattern_match.get("slots", {}),
        "query_fingerprint": pattern_match.get("query_fingerprint"),
        "sql_fingerprint": pattern_match.get("sql_fingerprint"),
        "execution_fingerprint": pattern_match.get("execution_fingerprint"),
        "cluster_count": len(pattern_match.get("clusters", [])),
        "cache_hit": cache_hit,
        "cache_stored": cache_stored,
    }


def _render_market_share_template(slots: Dict[str, dict]) -> Dict[str, Any]:
    numerator_params: List[Any] = []
    numerator_where_sql = ["(industry_performance.category != 'O' OR industry_performance.category IS NULL)"]
    numerator_where_param = ["(industry_performance.category != 'O' OR industry_performance.category IS NULL)"]
    denominator_params: List[Any] = []
    denominator_where_sql = ["(industry_performance.category != 'O' OR industry_performance.category IS NULL)"]
    denominator_where_param = ["(industry_performance.category != 'O' OR industry_performance.category IS NULL)"]
    dimensions = set(_slot_value(slots, "dimensions") or [])

    for column, slot_name in [
        ("industry_performance.fiscal_year", "fiscal_year"),
        ("industry_performance.statename", "state"),
        ("industry_performance.productname", "product"),
        ("industry_performance.sbu_name", "sbu"),
        ("industry_performance.distname", "district"),
    ]:
        value = _uppercase_slot_value(slots, slot_name) if slot_name == "sbu" else _slot_value(slots, slot_name)
        _append_condition(numerator_where_sql, numerator_where_param, numerator_params, column, "=", value)
        _append_condition(denominator_where_sql, denominator_where_param, denominator_params, column, "=", value)
    if not ({"zone", "zones"} & dimensions):
        zone = _industry_zone_value(_slot_value(slots, "zone"))
        _append_condition(numerator_where_sql, numerator_where_param, numerator_params, "industry_performance.zone_name", "=", zone)
        _append_condition(denominator_where_sql, denominator_where_param, denominator_params, "industry_performance.zone_name", "=", zone)

    # A named company narrows only the numerator. The denominator remains the
    # matching total market so company share cannot collapse to company/company.
    _append_condition(
        numerator_where_sql,
        numerator_where_param,
        numerator_params,
        "industry_performance.comname",
        "=",
        _slot_value(slots, "company"),
    )

    numerator_raw_where = " AND ".join(numerator_where_sql)
    numerator_param_where = " AND ".join(numerator_where_param)
    denominator_raw_where = " AND ".join(denominator_where_sql)
    denominator_param_where = " AND ".join(denominator_where_param)
    monthly = "month" in dimensions
    zonal = bool({"zone", "zones"} & dimensions)
    company_select = (
        "industry_performance.month_name, "
        if monthly
        else "industry_performance.zone_name, "
        if zonal
        else "industry_performance.comname, "
    )
    company_group = (
        "industry_performance.month_name"
        if monthly
        else "industry_performance.zone_name"
        if zonal
        else "industry_performance.comname"
    )
    denominator_select = "industry_performance.month_name, " if monthly else ""
    denominator_group = "\n    GROUP BY industry_performance.month_name" if monthly else ""
    final_dimension = (
        "company_sales.month_name"
        if monthly
        else "company_sales.zone_name"
        if zonal
        else "company_sales.comname"
    )
    denominator_join = (
        "INNER JOIN total_market ON company_sales.month_name IS NOT DISTINCT FROM total_market.month_name"
        if monthly
        else "CROSS JOIN total_market"
    )
    market_share_expression = FORMULA_ENGINE.market_share_sql(
        "company_sales.company_total",
        "total_market.market_total",
        alias="market_share_percentage",
    )
    direction = _enum_slot(slots, "order_direction", {"ASC", "DESC"}, default="ASC")
    limit = _int_slot(slots, "limit", default=1, low=1, high=500)

    sql = f"""WITH company_sales AS (
    SELECT {company_select}SUM(industry_performance.netweight_tmt) AS company_total
    FROM industry_performance
    WHERE {numerator_raw_where}
    GROUP BY {company_group}
),
total_market AS (
    SELECT {denominator_select}SUM(industry_performance.netweight_tmt) AS market_total
    FROM industry_performance
    WHERE {denominator_raw_where}{denominator_group}
)
SELECT {final_dimension},
       {market_share_expression}
FROM company_sales
{denominator_join}
ORDER BY market_share_percentage {direction}
LIMIT {limit};"""

    parameterized_sql = f"""WITH company_sales AS (
    SELECT {company_select}SUM(industry_performance.netweight_tmt) AS company_total
    FROM industry_performance
    WHERE {numerator_param_where}
    GROUP BY {company_group}
),
total_market AS (
    SELECT {denominator_select}SUM(industry_performance.netweight_tmt) AS market_total
    FROM industry_performance
    WHERE {denominator_param_where}{denominator_group}
)
SELECT {final_dimension},
       {market_share_expression}
FROM company_sales
{denominator_join}
ORDER BY market_share_percentage {direction}
LIMIT {limit};"""
    return {
        "sql": sql,
        "parameterized_sql": parameterized_sql,
        "params": numerator_params + denominator_params,
    }


def _render_actual_sales_template(slots: Dict[str, dict]) -> Dict[str, Any]:
    params: List[Any] = []
    where_sql = [
        "mom_level.sbu_name != '0'",
        "mom_level.sbu_name NOT IN ('Common', 'Mumbai Ref', 'Renewable Energy', 'Visakh Ref')",
    ]
    where_param = list(where_sql)
    _append_condition(where_sql, where_param, params, "mom_level.sbu_name", "=", _slot_value(slots, "sbu"))
    _append_condition(where_sql, where_param, params, "mom_level.productname", "=", _slot_value(slots, "product"))
    _append_condition(where_sql, where_param, params, "mom_level.fiscal_year", "=", _slot_value(slots, "fiscal_year"))
    _append_month_condition(where_sql, where_param, params, "mom_level", _slot_value(slots, "month"))
    _append_date_range(where_sql, where_param, params, "mom_level.day_id", _slot_value(slots, "date_range"))

    unit = _enum_slot(slots, "unit", {"TMT", "MT"}, default="TMT")
    alias = "sum_actual_sales_tmt"
    if unit == "MT":
        alias = "sum_actual_sales_mt"
    expression = FORMULA_ENGINE.actual_volume_sql(
        "mom_level.netweight_tmt",
        unit=unit,
        alias=None,
    )

    raw_where = " AND ".join(where_sql)
    param_where = " AND ".join(where_param)
    return {
        "sql": f"SELECT {expression} AS {alias}\nFROM mom_level\nWHERE {raw_where};",
        "parameterized_sql": f"SELECT {expression} AS {alias}\nFROM mom_level\nWHERE {param_where};",
        "params": params,
    }


def _render_sales_growth_comparison_template(slots: Dict[str, dict]) -> Dict[str, Any]:
    start_year = _slot_value(slots, "fiscal_year_start")
    end_year = _slot_value(slots, "fiscal_year_end")
    params: List[Any] = [start_year, end_year]
    where_sql = ["(industry_performance.category != 'O' OR industry_performance.category IS NULL)"]
    where_param = list(where_sql)
    _append_condition(where_sql, where_param, params, "industry_performance.comname", "=", _slot_value(slots, "company"))
    _append_condition(where_sql, where_param, params, "industry_performance.sbu_name", "=", _uppercase_slot_value(slots, "sbu"))
    _append_condition(where_sql, where_param, params, "industry_performance.productname", "=", _slot_value(slots, "product"))
    _append_condition(where_sql, where_param, params, "industry_performance.statename", "=", _slot_value(slots, "state"))
    _append_condition(where_sql, where_param, params, "industry_performance.distname", "=", _slot_value(slots, "district"))
    _append_condition(where_sql, where_param, params, "industry_performance.zone_name", "=", _industry_zone_value(_slot_value(slots, "zone")))
    _append_in_condition(
        where_sql,
        where_param,
        params,
        "industry_performance.fiscal_year",
        [start_year, end_year],
    )

    raw_where = " AND ".join(where_sql)
    param_where = " AND ".join(where_param)
    raw_previous_total = FORMULA_ENGINE.fiscal_year_total_sql(
        "industry_performance",
        "netweight_tmt",
        _literal(start_year),
    )
    raw_current_total = FORMULA_ENGINE.fiscal_year_total_sql(
        "industry_performance",
        "netweight_tmt",
        _literal(end_year),
    )
    param_previous_total = FORMULA_ENGINE.fiscal_year_total_sql(
        "industry_performance",
        "netweight_tmt",
        "%s",
    )
    param_current_total = FORMULA_ENGINE.fiscal_year_total_sql(
        "industry_performance",
        "netweight_tmt",
        "%s",
    )
    sales_difference = FORMULA_ENGINE.sales_difference_sql(
        "sales_totals.current_sales",
        "sales_totals.previous_sales",
        alias="sales_difference_tmt",
    )
    growth_percentage = FORMULA_ENGINE.growth_percentage_sql(
        "sales_totals.current_sales",
        "sales_totals.previous_sales",
        alias="sales_growth_percentage",
    )
    raw_sql = f"""WITH sales_totals AS (
    SELECT {raw_previous_total} AS previous_sales,
           {raw_current_total} AS current_sales
    FROM industry_performance
    WHERE {raw_where}
)
SELECT ROUND(sales_totals.previous_sales::numeric, 2) AS previous_sales_tmt,
       ROUND(sales_totals.current_sales::numeric, 2) AS current_sales_tmt,
       {sales_difference},
       {growth_percentage}
FROM sales_totals;"""
    parameterized_sql = f"""WITH sales_totals AS (
    SELECT {param_previous_total} AS previous_sales,
           {param_current_total} AS current_sales
    FROM industry_performance
    WHERE {param_where}
)
SELECT ROUND(sales_totals.previous_sales::numeric, 2) AS previous_sales_tmt,
       ROUND(sales_totals.current_sales::numeric, 2) AS current_sales_tmt,
       {sales_difference},
       {growth_percentage}
FROM sales_totals;"""
    return {"sql": raw_sql, "parameterized_sql": parameterized_sql, "params": params}


def _render_mom_product_growth_threshold_template(slots: Dict[str, dict]) -> Dict[str, Any]:
    start_year = _slot_value(slots, "fiscal_year_start")
    end_year = _slot_value(slots, "fiscal_year_end")
    threshold = _slot_value(slots, "growth_threshold")
    operator = _enum_slot(slots, "growth_operator", {">", "<", ">=", "<="}, default=">")
    params: List[Any] = [
        end_year, start_year,
        end_year, start_year, start_year,
        start_year, end_year,
        end_year, start_year, start_year, threshold,
    ]
    exclusions = (
        "mom_level.sbu_name != '0' AND "
        "mom_level.sbu_name NOT IN ('Common', 'Mumbai Ref', 'Renewable Energy', 'Visakh Ref')"
    )
    growth_expression = f"""(
        (
            SUM(CASE WHEN mom_level.fiscal_year = {_literal(end_year)} THEN mom_level.netweight_tmt ELSE 0 END)
            - SUM(CASE WHEN mom_level.fiscal_year = {_literal(start_year)} THEN mom_level.netweight_tmt ELSE 0 END)
        ) * 100.0
        / NULLIF(SUM(CASE WHEN mom_level.fiscal_year = {_literal(start_year)} THEN mom_level.netweight_tmt ELSE 0 END), 0)
    )"""
    parameterized_growth_expression = """(
        (
            SUM(CASE WHEN mom_level.fiscal_year = %s THEN mom_level.netweight_tmt ELSE 0 END)
            - SUM(CASE WHEN mom_level.fiscal_year = %s THEN mom_level.netweight_tmt ELSE 0 END)
        ) * 100.0
        / NULLIF(SUM(CASE WHEN mom_level.fiscal_year = %s THEN mom_level.netweight_tmt ELSE 0 END), 0)
    )"""
    raw_sql = f"""SELECT mom_level.productname,
       SUM(CASE WHEN mom_level.fiscal_year = {_literal(end_year)} THEN mom_level.netweight_tmt ELSE 0 END) AS current_sales_tmt,
       SUM(CASE WHEN mom_level.fiscal_year = {_literal(start_year)} THEN mom_level.netweight_tmt ELSE 0 END) AS previous_sales_tmt,
       ROUND({growth_expression}::numeric, 2) AS sales_growth_percentage
FROM mom_level
WHERE {exclusions}
  AND mom_level.fiscal_year IN ({_literal(start_year)}, {_literal(end_year)})
GROUP BY mom_level.productname
HAVING {growth_expression} {operator} {_literal(threshold)}
ORDER BY sales_growth_percentage DESC;"""
    parameterized_sql = f"""SELECT mom_level.productname,
       SUM(CASE WHEN mom_level.fiscal_year = %s THEN mom_level.netweight_tmt ELSE 0 END) AS current_sales_tmt,
       SUM(CASE WHEN mom_level.fiscal_year = %s THEN mom_level.netweight_tmt ELSE 0 END) AS previous_sales_tmt,
       ROUND({parameterized_growth_expression}::numeric, 2) AS sales_growth_percentage
FROM mom_level
WHERE {exclusions}
  AND mom_level.fiscal_year IN (%s, %s)
GROUP BY mom_level.productname
HAVING {parameterized_growth_expression} {operator} %s
ORDER BY sales_growth_percentage DESC;"""
    return {"sql": raw_sql, "parameterized_sql": parameterized_sql, "params": params}


def _render_industry_actual_sales_template(slots: Dict[str, dict]) -> Dict[str, Any]:
    params: List[Any] = []
    where_sql = ["(industry_performance.category != 'O' OR industry_performance.category IS NULL)"]
    where_param = list(where_sql)
    _append_condition(where_sql, where_param, params, "industry_performance.comname", "=", _slot_value(slots, "company"))
    _append_condition(where_sql, where_param, params, "industry_performance.sbu_name", "=", _uppercase_slot_value(slots, "sbu"))
    _append_condition(where_sql, where_param, params, "industry_performance.productname", "=", _slot_value(slots, "product"))
    _append_condition(where_sql, where_param, params, "industry_performance.fiscal_year", "=", _slot_value(slots, "fiscal_year"))
    _append_condition(where_sql, where_param, params, "industry_performance.statename", "=", _slot_value(slots, "state"))
    _append_condition(where_sql, where_param, params, "industry_performance.distname", "=", _slot_value(slots, "district"))
    _append_condition(where_sql, where_param, params, "industry_performance.zone_name", "=", _industry_zone_value(_slot_value(slots, "zone")))
    _append_month_condition(where_sql, where_param, params, "industry_performance", _slot_value(slots, "month"))

    unit = _enum_slot(slots, "unit", {"TMT", "MT"}, default="TMT")
    alias = "sum_actual_sales_tmt"
    if unit == "MT":
        alias = "sum_actual_sales_mt"
    expression = FORMULA_ENGINE.actual_volume_sql(
        "industry_performance.netweight_tmt",
        unit=unit,
        alias=None,
    )

    raw_where = " AND ".join(where_sql)
    param_where = " AND ".join(where_param)
    return {
        "sql": f"SELECT {expression} AS {alias}\nFROM industry_performance\nWHERE {raw_where};",
        "parameterized_sql": f"SELECT {expression} AS {alias}\nFROM industry_performance\nWHERE {param_where};",
        "params": params,
    }


def _render_target_quantity_template(slots: Dict[str, dict]) -> Dict[str, Any]:
    params: List[Any] = []
    where_sql = ["1 = 1"]
    where_param = ["1 = 1"]
    _append_case_insensitive_condition(where_sql, where_param, params, "m60_level.sbu_name", _slot_value(slots, "sbu"))
    _append_case_insensitive_condition(where_sql, where_param, params, "m60_level.zone_name", _slot_value(slots, "zone"))
    _append_case_insensitive_condition(where_sql, where_param, params, "m60_level.productname", _slot_value(slots, "product"))
    _append_condition(where_sql, where_param, params, "m60_level.fiscal_year", "=", _slot_value(slots, "fiscal_year"))
    _append_month_condition(where_sql, where_param, params, "m60_level", _slot_value(slots, "month"))

    unit = _enum_slot(slots, "unit", {"KL", "TMT"}, default="TMT")
    column = "target_qty_kl" if unit == "KL" else "target_qty_tmt"
    alias = "sum_target_quantity_kl" if unit == "KL" else "sum_target_quantity_tmt"
    raw_where = " AND ".join(where_sql)
    param_where = " AND ".join(where_param)
    return {
        "sql": f"SELECT SUM(m60_level.{column}) AS {alias}\nFROM m60_level\nWHERE {raw_where};",
        "parameterized_sql": f"SELECT SUM(m60_level.{column}) AS {alias}\nFROM m60_level\nWHERE {param_where};",
        "params": params,
    }


def _render_target_actual_variance_template(slots: Dict[str, dict]) -> Dict[str, Any]:
    params: List[Any] = []
    where_sql = ["1 = 1"]
    where_param = ["1 = 1"]
    _append_case_insensitive_condition(where_sql, where_param, params, "m60_level.sbu_name", _slot_value(slots, "sbu"))
    _append_case_insensitive_condition(where_sql, where_param, params, "m60_level.zone_name", _slot_value(slots, "zone"))
    _append_case_insensitive_condition(where_sql, where_param, params, "m60_level.productname", _slot_value(slots, "product"))
    _append_condition(where_sql, where_param, params, "m60_level.fiscal_year", "=", _slot_value(slots, "fiscal_year"))
    _append_month_condition(where_sql, where_param, params, "m60_level", _slot_value(slots, "month"))

    dimensions = set(_slot_value(slots, "dimensions") or [])
    dimension_columns = []
    if {"region", "regions"} & dimensions:
        dimension_columns.append("m60_level.region_name")
    if {"month", "months"} & dimensions:
        dimension_columns.append("m60_level.month_name")
    if {"zone", "zones"} & dimensions:
        dimension_columns.append("m60_level.zone_name")
    if {"sbu", "strategic business unit"} & dimensions:
        dimension_columns.append("m60_level.sbu_name")
    if {"product", "products"} & dimensions:
        dimension_columns.append("m60_level.productname")

    prefix = f"{', '.join(dimension_columns)}, " if dimension_columns else ""
    group_by = f"\nGROUP BY {', '.join(dimension_columns)}" if dimension_columns else ""
    raw_where = " AND ".join(where_sql)
    param_where = " AND ".join(where_param)
    aggregation = _enum_slot(slots, "comparison_aggregation", {"SUM", "AVG"}, default="SUM")
    target = f"{aggregation}(NULLIF(m60_level.target_qty_tmt, 'NaN'::numeric))"
    actual = f"{aggregation}(NULLIF(m60_level.actual_round, 'NaN'::numeric))"
    select_metrics = f"""ROUND({target}::numeric, 2) AS target_quantity_tmt,
       ROUND({actual}::numeric, 2) AS actual_sales_tmt,
       ROUND(({actual} - {target})::numeric, 2) AS variance_tmt,
       ROUND((({actual} - {target}) * 100.0 /
              NULLIF({target}, 0))::numeric, 2) AS variance_percentage"""
    return {
        "sql": f"SELECT {prefix}{select_metrics}\nFROM m60_level\nWHERE {raw_where}{group_by};",
        "parameterized_sql": f"SELECT {prefix}{select_metrics}\nFROM m60_level\nWHERE {param_where}{group_by};",
        "params": params,
    }


def _build_where_clause(table: str, slots: Dict[str, dict]) -> Tuple[List[str], List[str], List[Any]]:
    params: List[Any] = []
    if table == "industry_performance":
        where_sql = ["(industry_performance.category != 'O' OR industry_performance.category IS NULL)"]
        where_param = list(where_sql)
        _append_condition(where_sql, where_param, params, "industry_performance.comname", "=", _slot_value(slots, "company"))
        _append_condition(where_sql, where_param, params, "industry_performance.sbu_name", "=", _uppercase_slot_value(slots, "sbu"))
        _append_condition(where_sql, where_param, params, "industry_performance.productname", "=", _slot_value(slots, "product"))
        _append_condition(where_sql, where_param, params, "industry_performance.statename", "=", _slot_value(slots, "state"))
        _append_condition(where_sql, where_param, params, "industry_performance.distname", "=", _slot_value(slots, "district"))
        _append_condition(where_sql, where_param, params, "industry_performance.zone_name", "=", _industry_zone_value(_slot_value(slots, "zone")))
        _append_condition(where_sql, where_param, params, "industry_performance.region_name", "=", _slot_value(slots, "region"))
        _append_condition(where_sql, where_param, params, "industry_performance.fiscal_year", "=", _slot_value(slots, "fiscal_year"))
    else:
        where_sql = [
            "mom_level.sbu_name != '0'",
            "mom_level.sbu_name NOT IN ('Common', 'Mumbai Ref', 'Renewable Energy', 'Visakh Ref')",
        ]
        where_param = list(where_sql)
        _append_condition(where_sql, where_param, params, "mom_level.sbu_name", "=", _slot_value(slots, "sbu"))
        _append_condition(where_sql, where_param, params, "mom_level.productname", "=", _slot_value(slots, "product"))
        _append_condition(where_sql, where_param, params, "mom_level.zone_name", "=", _slot_value(slots, "zone"))
        _append_condition(where_sql, where_param, params, "mom_level.region_name", "=", _slot_value(slots, "region"))
        _append_condition(where_sql, where_param, params, "mom_level.fiscal_year", "=", _slot_value(slots, "fiscal_year"))
    return where_sql, where_param, params


def _render_monthly_trend_template(slots: Dict[str, dict], table: str) -> Dict[str, Any]:
    where_sql, where_param, params = _build_where_clause(table, slots)
    unit = _enum_slot(slots, "unit", {"TMT", "MT"}, default="TMT")
    alias = "actual_sales_tmt" if unit == "TMT" else "actual_sales_mt"
    expression = FORMULA_ENGINE.actual_volume_sql(f"{table}.netweight_tmt", unit=unit, alias=None)

    raw_where = " AND ".join(where_sql)
    param_where = " AND ".join(where_param)

    if table == "industry_performance":
        sql = f"""SELECT month_name, {expression} AS {alias}
FROM industry_performance
WHERE {raw_where}
GROUP BY month_name
ORDER BY CASE month_name 
  WHEN 'APR' THEN 1 WHEN 'MAY' THEN 2 WHEN 'JUN' THEN 3 WHEN 'JUL' THEN 4 
  WHEN 'AUG' THEN 5 WHEN 'SEP' THEN 6 WHEN 'OCT' THEN 7 WHEN 'NOV' THEN 8 
  WHEN 'DEC' THEN 9 WHEN 'JAN' THEN 10 WHEN 'FEB' THEN 11 WHEN 'MAR' THEN 12 
END;"""
        parameterized_sql = f"""SELECT month_name, {expression} AS {alias}
FROM industry_performance
WHERE {param_where}
GROUP BY month_name
ORDER BY CASE month_name 
  WHEN 'APR' THEN 1 WHEN 'MAY' THEN 2 WHEN 'JUN' THEN 3 WHEN 'JUL' THEN 4 
  WHEN 'AUG' THEN 5 WHEN 'SEP' THEN 6 WHEN 'OCT' THEN 7 WHEN 'NOV' THEN 8 
  WHEN 'DEC' THEN 9 WHEN 'JAN' THEN 10 WHEN 'FEB' THEN 11 WHEN 'MAR' THEN 12 
END;"""
    else:
        sql = f"""SELECT month_name, {expression} AS {alias}
FROM mom_level
WHERE {raw_where}
GROUP BY month_name, yearmonth
ORDER BY yearmonth;"""
        parameterized_sql = f"""SELECT month_name, {expression} AS {alias}
FROM mom_level
WHERE {param_where}
GROUP BY month_name, yearmonth
ORDER BY yearmonth;"""

    return {"sql": sql, "parameterized_sql": parameterized_sql, "params": params}


def _render_quarterly_trend_template(slots: Dict[str, dict], table: str) -> Dict[str, Any]:
    where_sql, where_param, params = _build_where_clause(table, slots)
    unit = _enum_slot(slots, "unit", {"TMT", "MT"}, default="TMT")
    alias = "actual_sales_tmt" if unit == "TMT" else "actual_sales_mt"
    expression = FORMULA_ENGINE.actual_volume_sql(f"{table}.netweight_tmt", unit=unit, alias=None)

    raw_where = " AND ".join(where_sql)
    param_where = " AND ".join(where_param)

    quarter_case = """CASE WHEN month_name IN ('APR', 'MAY', 'JUN') THEN 'Q1' 
       WHEN month_name IN ('JUL', 'AUG', 'SEP') THEN 'Q2' 
       WHEN month_name IN ('OCT', 'NOV', 'DEC') THEN 'Q3' 
       ELSE 'Q4' END"""

    sql = f"""SELECT {quarter_case} AS fiscal_quarter, {expression} AS {alias}
FROM {table}
WHERE {raw_where}
GROUP BY fiscal_quarter
ORDER BY fiscal_quarter;"""

    parameterized_sql = f"""SELECT {quarter_case} AS fiscal_quarter, {expression} AS {alias}
FROM {table}
WHERE {param_where}
GROUP BY fiscal_quarter
ORDER BY fiscal_quarter;"""

    return {"sql": sql, "parameterized_sql": parameterized_sql, "params": params}


def _render_yearly_trend_template(slots: Dict[str, dict], table: str) -> Dict[str, Any]:
    where_sql, where_param, params = _build_where_clause(table, slots)
    unit = _enum_slot(slots, "unit", {"TMT", "MT"}, default="TMT")
    alias = "actual_sales_tmt" if unit == "TMT" else "actual_sales_mt"
    expression = FORMULA_ENGINE.actual_volume_sql(f"{table}.netweight_tmt", unit=unit, alias=None)

    raw_where = " AND ".join(where_sql)
    param_where = " AND ".join(where_param)

    sql = f"""SELECT fiscal_year, {expression} AS {alias}
FROM {table}
WHERE {raw_where}
GROUP BY fiscal_year
ORDER BY fiscal_year;"""

    parameterized_sql = f"""SELECT fiscal_year, {expression} AS {alias}
FROM {table}
WHERE {param_where}
GROUP BY fiscal_year
ORDER BY fiscal_year;"""

    return {"sql": sql, "parameterized_sql": parameterized_sql, "params": params}


def _render_highest_lowest_month_template(slots: Dict[str, dict], table: str) -> Dict[str, Any]:
    where_sql, where_param, params = _build_where_clause(table, slots)
    unit = _enum_slot(slots, "unit", {"TMT", "MT"}, default="TMT")
    alias = "actual_sales_tmt" if unit == "TMT" else "actual_sales_mt"
    expression = FORMULA_ENGINE.actual_volume_sql(f"{table}.netweight_tmt", unit=unit, alias=None)

    direction = _enum_slot(slots, "order_direction", {"ASC", "DESC"}, default="DESC")
    limit = _int_slot(slots, "limit", default=1, low=1, high=500)

    raw_where = " AND ".join(where_sql)
    param_where = " AND ".join(where_param)

    sql = f"""SELECT month_name, {expression} AS {alias}
FROM {table}
WHERE {raw_where}
GROUP BY month_name
ORDER BY {alias} {direction}
LIMIT {limit};"""

    parameterized_sql = f"""SELECT month_name, {expression} AS {alias}
FROM {table}
WHERE {param_where}
GROUP BY month_name
ORDER BY {alias} {direction}
LIMIT {limit};"""

    return {"sql": sql, "parameterized_sql": parameterized_sql, "params": params}


def _render_mom_growth_template(slots: Dict[str, dict], table: str) -> Dict[str, Any]:
    where_sql, where_param, params = _build_where_clause(table, slots)
    unit = _enum_slot(slots, "unit", {"TMT", "MT"}, default="TMT")
    sales_alias = "sales_tmt" if unit == "TMT" else "sales_mt"
    prev_sales_alias = "previous_month_sales_tmt" if unit == "TMT" else "previous_month_sales_mt"
    
    raw_where = " AND ".join(where_sql)
    param_where = " AND ".join(where_param)

    expression = f"{table}.netweight_tmt" if unit == "TMT" else f"{table}.netweight_tmt * 1000"

    if table == "industry_performance":
        sql = f"""WITH monthly_sales AS (
  SELECT 
    month_name, 
    SUM({expression}) AS {sales_alias},
    CASE month_name 
      WHEN 'APR' THEN 1 WHEN 'MAY' THEN 2 WHEN 'JUN' THEN 3 WHEN 'JUL' THEN 4 
      WHEN 'AUG' THEN 5 WHEN 'SEP' THEN 6 WHEN 'OCT' THEN 7 WHEN 'NOV' THEN 8 
      WHEN 'DEC' THEN 9 WHEN 'JAN' THEN 10 WHEN 'FEB' THEN 11 WHEN 'MAR' THEN 12 
    END AS month_idx
  FROM industry_performance
  WHERE {raw_where}
  GROUP BY month_name
)
SELECT 
  month_name,
  {sales_alias},
  LAG({sales_alias}) OVER (ORDER BY month_idx) AS {prev_sales_alias},
  ROUND((({sales_alias} - LAG({sales_alias}) OVER (ORDER BY month_idx)) * 100.0) / NULLIF(LAG({sales_alias}) OVER (ORDER BY month_idx), 0), 2) AS mom_growth_percentage
FROM monthly_sales
ORDER BY month_idx;"""

        parameterized_sql = f"""WITH monthly_sales AS (
  SELECT 
    month_name, 
    SUM({expression}) AS {sales_alias},
    CASE month_name 
      WHEN 'APR' THEN 1 WHEN 'MAY' THEN 2 WHEN 'JUN' THEN 3 WHEN 'JUL' THEN 4 
      WHEN 'AUG' THEN 5 WHEN 'SEP' THEN 6 WHEN 'OCT' THEN 7 WHEN 'NOV' THEN 8 
      WHEN 'DEC' THEN 9 WHEN 'JAN' THEN 10 WHEN 'FEB' THEN 11 WHEN 'MAR' THEN 12 
    END AS month_idx
  FROM industry_performance
  WHERE {param_where}
  GROUP BY month_name
)
SELECT 
  month_name,
  {sales_alias},
  LAG({sales_alias}) OVER (ORDER BY month_idx) AS {prev_sales_alias},
  ROUND((({sales_alias} - LAG({sales_alias}) OVER (ORDER BY month_idx)) * 100.0) / NULLIF(LAG({sales_alias}) OVER (ORDER BY month_idx), 0), 2) AS mom_growth_percentage
FROM monthly_sales
ORDER BY month_idx;"""
    else:
        sql = f"""WITH monthly_sales AS (
  SELECT month_name, yearmonth, SUM({expression}) AS {sales_alias}
  FROM mom_level
  WHERE {raw_where}
  GROUP BY month_name, yearmonth
)
SELECT 
  month_name,
  {sales_alias},
  LAG({sales_alias}) OVER (ORDER BY yearmonth) AS {prev_sales_alias},
  ROUND((({sales_alias} - LAG({sales_alias}) OVER (ORDER BY yearmonth)) * 100.0) / NULLIF(LAG({sales_alias}) OVER (ORDER BY yearmonth), 0), 2) AS mom_growth_percentage
FROM monthly_sales
ORDER BY yearmonth;"""

        parameterized_sql = f"""WITH monthly_sales AS (
  SELECT month_name, yearmonth, SUM({expression}) AS {sales_alias}
  FROM mom_level
  WHERE {param_where}
  GROUP BY month_name, yearmonth
)
SELECT 
  month_name,
  {sales_alias},
  LAG({sales_alias}) OVER (ORDER BY yearmonth) AS {prev_sales_alias},
  ROUND((({sales_alias} - LAG({sales_alias}) OVER (ORDER BY yearmonth)) * 100.0) / NULLIF(LAG({sales_alias}) OVER (ORDER BY yearmonth), 0), 2) AS mom_growth_percentage
FROM monthly_sales
ORDER BY yearmonth;"""

    return {"sql": sql, "parameterized_sql": parameterized_sql, "params": params}


def _score_template(
    template: QueryTemplate,
    question: str,
    routing: dict,
    slots: Dict[str, dict],
    clusters: Sequence[dict],
) -> Tuple[float, List[str]]:
    q_norm = _normalize(question)
    metric_name = _slot_value(slots, "metric")
    score = 0.0
    reasons: List[str] = []

    if metric_name == template.metric_name:
        score += 0.58
        reasons.append("metric_match")

    if template.template_id == "industry_market_share_rank_v1":
        if "market share" in q_norm or "market contribution" in q_norm:
            score += 0.24
            reasons.append("market_ratio_keyword")
        if "company" in q_norm or "companies" in q_norm:
            score += 0.08
            reasons.append("company_dimension")
    elif template.template_id == "industry_sales_growth_comparison_v1":
        if _has_sales_comparison_intent(q_norm):
            score += 0.30
            reasons.append("sales_comparison_keyword")
        if _slot_value(slots, "company"):
            score += 0.12
            reasons.append("company_filter")
    elif template.template_id == "mom_product_growth_threshold_v1":
        if _has_product_growth_threshold_intent(q_norm, slots):
            score += 0.40
            reasons.append("product_growth_threshold_keyword")
    elif template.template_id == "mom_actual_sales_sum_v1":
        if any(term in q_norm for term in ["actual", "sales", "netweight", "volume"]):
            score += 0.20
            reasons.append("actual_sales_keyword")
        if _slot_value(slots, "date_range") or _slot_value(slots, "product") or _slot_value(slots, "sbu"):
            score += 0.10
            reasons.append("filter_slots")
    elif template.template_id == "industry_actual_sales_sum_v1":
        if any(term in q_norm for term in ["actual", "sales", "netweight", "volume"]):
            score += 0.20
            reasons.append("actual_sales_keyword")
        if _slot_value(slots, "company"):
            score += 0.16
            reasons.append("company_filter")
        if _slot_value(slots, "product") or _slot_value(slots, "sbu") or _slot_value(slots, "fiscal_year"):
            score += 0.10
            reasons.append("filter_slots")
    elif template.template_id == "m60_target_quantity_sum_v1":
        if "target" in q_norm:
            score += 0.24
            reasons.append("target_keyword")
        if _slot_value(slots, "zone") or _slot_value(slots, "sbu"):
            score += 0.08
            reasons.append("filter_slots")
    elif template.template_id == "m60_target_actual_variance_v1":
        if _has_target_actual_comparison_intent(q_norm):
            score += 0.40
            reasons.append("target_actual_comparison_keyword")
    elif template.template_id == "industry_declining_states_v1":
        if any(term in q_norm for term in ["declining", "decline", "negative growth", "dropping", "fallen", "fell", "shrinking", "growing", "improved", "increasing"]):
            score += 0.45
            reasons.append("decline_keyword")
        if "state" in q_norm or "states" in q_norm:
            score += 0.15
            reasons.append("state_dimension")
    elif template.template_id == "mom_product_best_growth_v1":
        has_product = "product" in q_norm or "products" in q_norm
        has_mom = any(term in q_norm for term in ["mom", "month over month", "monthly growth", "mom growth"])
        has_growth = any(term in q_norm for term in ["growth", "grew", "highest", "lowest", "best", "worst"])
        if has_product and has_mom:
            score += 0.87  # strong specific signal: product + MOM keyword together
            reasons.append("product_mom_growth_keyword")
        elif has_product and has_growth and any(term in q_norm for term in ["month", "monthly"]):
            score += 0.72
            reasons.append("product_monthly_growth_keyword")
        # If no metric was extracted but intent is trend_analysis and product is present, give base boost
        if metric_name is None and routing.get("intent") == "trend_analysis" and has_product:
            score += 0.10
            reasons.append("trend_product_fallback")
    elif template.template_id in {
        "industry_monthly_trend_v1", "mom_monthly_trend_v1",
        "industry_quarterly_trend_v1", "mom_quarterly_trend_v1",
        "industry_yearly_trend_v1", "mom_yearly_trend_v1",
        "industry_highest_lowest_month_v1", "mom_highest_lowest_month_v1",
        "industry_mom_growth_v1", "mom_mom_growth_v1"
    }:
        t_type = _slot_value(slots, "trend_type")
        t_metric = _slot_value(slots, "trend_metric")
        
        is_match = False
        if template.template_id in {"industry_monthly_trend_v1", "mom_monthly_trend_v1"}:
            is_match = (t_type == "monthly" and t_metric is None)
        elif template.template_id in {"industry_quarterly_trend_v1", "mom_quarterly_trend_v1"}:
            is_match = (t_type == "quarterly" and t_metric is None)
        elif template.template_id in {"industry_yearly_trend_v1", "mom_yearly_trend_v1"}:
            is_match = (t_type == "yearly" and t_metric is None)
        elif template.template_id in {"industry_highest_lowest_month_v1", "mom_highest_lowest_month_v1"}:
            is_match = (t_type == "monthly" and t_metric == "highest_lowest")
        elif template.template_id in {"industry_mom_growth_v1", "mom_mom_growth_v1"}:
            is_match = (t_type == "monthly" and t_metric == "growth")
            
        if is_match:
            score += 0.40
            reasons.append("trend_match")

    source_tables = {item.get("table") for item in routing.get("source_tables", [])}
    if template.template_id.startswith("industry") and "industry_performance" in source_tables:
        score += 0.06
        reasons.append("source_table_match")
    if template.template_id.startswith("mom") and "mom_level" in source_tables:
        score += 0.06
        reasons.append("source_table_match")
    if template.template_id.startswith("m60") and "m60_level" in source_tables:
        score += 0.06
        reasons.append("source_table_match")

    if clusters and clusters[0].get("best_similarity", 0.0) >= 0.98:
        score += 0.06
        reasons.append("high_similarity_cluster")

    return min(score, 0.99), reasons


def _template_slots_available(template: QueryTemplate, slots: Dict[str, dict]) -> bool:
    # Templates that are strictly SBU-only (mom_level) vs company-specific (industry_performance)
    mom_sbu_only = {
        "mom_actual_sales_sum_v1",
        "mom_monthly_trend_v1", "mom_quarterly_trend_v1", "mom_yearly_trend_v1",
        "mom_highest_lowest_month_v1", "mom_mom_growth_v1",
    }
    industry_company_required = {
        "industry_actual_sales_sum_v1",
        "industry_monthly_trend_v1", "industry_quarterly_trend_v1", "industry_yearly_trend_v1",
        "industry_highest_lowest_month_v1", "industry_mom_growth_v1",
    }
    has_company = _slot_value(slots, "company") is not None
    if template.template_id in mom_sbu_only and has_company:
        return False
    if template.template_id in industry_company_required and not has_company:
        return False

    if template.template_id in {"mom_actual_sales_sum_v1", "industry_actual_sales_sum_v1"}:
        if _slot_value(slots, "trend_type") is not None:
            return False
        if _has_sales_comparison_intent_from_slots(slots):
            return False
    if template.template_id == "mom_actual_sales_sum_v1" and _slot_value(slots, "company") is not None:
        return False
    if template.template_id == "m60_target_quantity_sum_v1" and _has_target_actual_comparison_intent_from_slots(slots):
        return False
    if template.template_id == "industry_sales_growth_comparison_v1" and _has_product_growth_threshold_intent("", slots):
        return False
    if template.template_id == "mom_product_growth_threshold_v1" and not _has_product_growth_threshold_intent("", slots):
        return False
    if template.template_id == "industry_declining_states_v1":
        return True  # no required_slots; always potentially available
    return all(_slot_value(slots, slot_name) is not None for slot_name in template.required_slots)


def _append_condition(
    raw_conditions: List[str],
    param_conditions: List[str],
    params: List[Any],
    column: str,
    operator: str,
    value: Any,
) -> None:
    if value is None or value == "":
        return
    if isinstance(value, dict):
        return
    if "zone_name" in column and "industry_performance" not in column:
        val_str = str(value).strip()
        if val_str in ("North West Frontier", "North Frontier Zone", "North Frontier"):
            value = "North"
        elif val_str in ("South Western Zone", "South Western"):
            value = "South"
    raw_conditions.append(f"{column} {operator} {_literal(value)}")
    param_conditions.append(f"{column} {operator} %s")
    params.append(value)


def _append_case_insensitive_condition(
    raw_conditions: List[str],
    param_conditions: List[str],
    params: List[Any],
    column: str,
    value: Any,
) -> None:
    if value is None or value == "" or isinstance(value, dict):
        return
    if "zone_name" in column and "industry_performance" not in column:
        val_str = str(value).strip()
        if val_str in ("North West Frontier", "North Frontier Zone", "North Frontier"):
            value = "North"
        elif val_str in ("South Western Zone", "South Western"):
            value = "South"
    raw_conditions.append(f"UPPER({column}) = UPPER({_literal(value)})")
    param_conditions.append(f"UPPER({column}) = UPPER(%s)")
    params.append(value)


def _append_in_condition(
    raw_conditions: List[str],
    param_conditions: List[str],
    params: List[Any],
    column: str,
    values: Sequence[Any],
) -> None:
    if not values:
        return
    raw_conditions.append(f"{column} IN ({', '.join(_literal(value) for value in values)})")
    param_conditions.append(f"{column} IN ({', '.join('%s' for _value in values)})")
    params.extend(values)


def _has_sales_comparison_intent(q_norm: str) -> bool:
    return any(term in q_norm for term in [
        "growth", "difference", "compare", "against", "year over year", "year-on-year", "yoy",
    ])


def _has_product_growth_threshold_intent(q_norm: str, slots: Dict[str, dict]) -> bool:
    dimensions = set(_slot_value(slots, "dimensions") or [])
    return (
        bool({"product", "products"} & dimensions)
        and _slot_value(slots, "growth_threshold") is not None
        and _has_sales_comparison_intent_from_slots(slots)
    )


def _extract_growth_threshold(q_norm: str) -> Optional[Tuple[str, float]]:
    patterns = [
        (">", r"\b(?:more than|greater than|above|over|exceed(?:ed|ing|s)?)\s+(\d+(?:\.\d+)?)\s*%"),
        ("<", r"\b(?:less than|below|under)\s+(\d+(?:\.\d+)?)\s*%"),
        (">=", r"\b(?:at least|minimum of)\s+(\d+(?:\.\d+)?)\s*%"),
        ("<=", r"\b(?:at most|maximum of)\s+(\d+(?:\.\d+)?)\s*%"),
    ]
    for operator, pattern in patterns:
        match = re.search(pattern, q_norm)
        if match:
            return operator, float(match.group(1))
    return None


def _has_target_actual_comparison_intent(q_norm: str) -> bool:
    return "target" in q_norm and "actual" in q_norm and any(
        term in q_norm for term in ["compare", "versus", "vs", "variance", "difference", "achievement"]
    )


def _has_target_actual_comparison_intent_from_slots(slots: Dict[str, dict]) -> bool:
    return _slot_value(slots, "metric") == "target_quantity" and _slot_value(slots, "actual_comparison") is True


def _has_sales_comparison_intent_from_slots(slots: Dict[str, dict]) -> bool:
    return _slot_value(slots, "fiscal_year_start") is not None and _slot_value(slots, "fiscal_year_end") is not None


def _uppercase_slot_value(slots: Dict[str, dict], slot_name: str) -> Any:
    value = _slot_value(slots, slot_name)
    return value.upper() if isinstance(value, str) else value


def _industry_zone_value(value: Any) -> Any:
    return {
        "North": "NZ",
        "South": "SZ",
        "East": "EZ",
        "West": "WZ",
    }.get(value, value)


def _labeled_context(q_norm: str, value: str, label: str) -> bool:
    return bool(
        re.search(rf"\b{re.escape(value)}\b\s+{label}\b", q_norm)
        or re.search(rf"\b{label}\b\s+(?:is\s+)?\b{re.escape(value)}\b", q_norm)
    )


def _fiscal_year_start(value: Any) -> int:
    match = re.match(r"^(\d{4})-", str(value))
    return int(match.group(1)) if match else 0


def _append_date_range(
    raw_conditions: List[str],
    param_conditions: List[str],
    params: List[Any],
    column: str,
    value: Any,
) -> None:
    if not isinstance(value, dict) or not value.get("start") or not value.get("end"):
        return
    raw_conditions.append(f"{column} BETWEEN {_literal(value['start'])} AND {_literal(value['end'])}")
    param_conditions.append(f"{column} BETWEEN %s AND %s")
    params.extend([value["start"], value["end"]])


def _append_month_condition(
    raw_conditions: List[str],
    param_conditions: List[str],
    params: List[Any],
    table: str,
    value: Any,
) -> None:
    if not isinstance(value, dict) or value.get("dynamic"):
        return
    if value.get("yearmonth"):
        _append_condition(raw_conditions, param_conditions, params, f"{table}.yearmonth", "=", value["yearmonth"])
    elif value.get("month_name"):
        _append_condition(raw_conditions, param_conditions, params, f"{table}.month_name", "=", value["month_name"])


def _set_slot(
    slots: Dict[str, dict],
    name: str,
    value: Any,
    source: str,
    confidence: float,
    dynamic: bool = False,
) -> None:
    if value is None:
        return
    existing = slots.get(name)
    if existing and existing.get("confidence", 0.0) > confidence:
        return
    if (
        existing
        and existing.get("confidence", 0.0) == confidence
        and isinstance(existing.get("value"), str)
        and isinstance(value, str)
        and len(existing["value"]) >= len(value)
    ):
        return
    slots[name] = {
        "value": value,
        "source": source,
        "confidence": round(confidence, 4),
        "dynamic": dynamic,
    }


def _slot_value(slots: Dict[str, dict], name: str, default: Any = None) -> Any:
    return slots.get(name, {}).get("value", default)


def _enum_slot(slots: Dict[str, dict], name: str, allowed: Iterable[str], default: str) -> str:
    value = str(_slot_value(slots, name, default)).upper()
    return value if value in set(allowed) else default


def _int_slot(slots: Dict[str, dict], name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(_slot_value(slots, name, default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, low), high)


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _public_slots(slots: Dict[str, dict]) -> Dict[str, Any]:
    return {
        name: {
            "value": data.get("value"),
            "source": data.get("source"),
            "confidence": data.get("confidence"),
            "dynamic": data.get("dynamic", False),
        }
        for name, data in sorted(slots.items())
    }


def _example_similarity(example: dict) -> float:
    for key in ["similarity", "rerank_score", "hybrid_score", "retrieval_confidence"]:
        try:
            return float(example.get(key, 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
    return 0.0


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())
