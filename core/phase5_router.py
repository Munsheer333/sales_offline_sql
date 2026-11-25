"""
NLPSQL SYSTEM — PHASE 05
Query Complexity Router & Classifier
"""

import asyncio
from datetime import date
import re
import requests
from config import CURRENT_FINANCIAL_YEAR, OLLAMA_GENERATE_URL
from typing import Any, Dict, List, Optional, Sequence

OLLAMA_URL = OLLAMA_GENERATE_URL

SYSTEM_PROMPT_PHASE5 = """You are a SQL complexity classifier for an enterprise NL-to-SQL system.

Classify the user's natural language question into exactly ONE of these complexity levels:

SIMPLE   — Single table lookup, basic WHERE/ORDER BY, no joins, no aggregation
MEDIUM   — 2-table JOIN, or basic aggregation (COUNT/SUM/AVG), or simple GROUP BY
HARD     — 3+ table JOINs, subqueries, HAVING clause, multiple aggregations
ADVANCED — Window functions (ROW_NUMBER/RANK/LAG/LEAD), CTEs, percentiles, time series
ULTRA    — Recursive CTEs, correlated subqueries, multi-step pipelines, pivot-style analysis

Output ONLY the level label, nothing else. Example: ADVANCED
"""

COMPLEXITY_PROMPT_TEMPLATES = {
    "SIMPLE": """Generate a simple PostgreSQL SELECT query.
Use a single table with basic WHERE, ORDER BY, and LIMIT clauses.
No JOINs, no subqueries, no aggregations unless explicitly needed.
Return only the SQL.""",

    "MEDIUM": """Generate a PostgreSQL query involving:
- A JOIN between 2 tables, OR
- Basic aggregation (COUNT, SUM, AVG, MAX, MIN) with GROUP BY
Keep it straightforward. Return only the SQL.""",

    "HARD": """Generate a complex PostgreSQL query involving:
- Multiple JOINs (3+ tables), AND/OR
- Subqueries in WHERE or FROM clause, AND/OR
- HAVING clause with conditions on aggregates
Use proper table aliases. Return only the SQL.""",

    "ADVANCED": """Generate an advanced PostgreSQL analytical query using:
- Window functions (ROW_NUMBER, RANK, DENSE_RANK, LAG, LEAD, SUM OVER, AVG OVER PARTITION BY)
- CTEs (WITH clauses) for multi-step logic
- Complex GROUP BY with ROLLUP or GROUPING SETS if appropriate
Return only the SQL.""",

    "ULTRA": """Generate a highly complex PostgreSQL analytical query using:
- Multiple CTEs chained together
- Correlated subqueries where needed
- Window functions combined with aggregations
- Multi-step analytical pipeline (compute intermediate results, then aggregate further)
- Use FILTER clauses on aggregates if appropriate
Break down the problem into logical CTE steps. Return only the SQL."""
}


VALID_COMPLEXITIES = {"SIMPLE", "MEDIUM", "HARD", "ADVANCED", "ULTRA"}
ROUTING_VERSION = "hybrid-router-v1"

COMPANY_NAMES = [
    "HPCL", "IOCL", "BPCL", "SHELL", "GAIL", "CPCL", "HMEL", "MRPL",
    "NEL", "NRL", "RIL", "RELIANCE", "NAYARA", "ONGC", "SMA", "OIL",
]
PRODUCT_NAMES = ["HSD", "MS", "LPG", "PROPANE", "NAPHTHA", "SKO", "ATF", "LDO", "FO"]
SBU_VALUES = {
    "retail": "Retail",
    "i&c": "I&C",
    "i and c": "I&C",
    "ic": "I&C",
    "lpg": "LPG",
    "aviation": "Aviation",
    "petchem": "Petchem",
    "gas": "Gas",
    "lubes": "Lubes",
}
STATE_ALIASES = {
    "ap": "ANDHRA PRADESH",
    "andhra pradesh": "ANDHRA PRADESH",
    "assam": "ASSAM",
    "bihar": "BIHAR",
    "delhi": "DELHI",
    "gujarat": "GUJARAT",
    "haryana": "HARYANA",
    "karnataka": "KARNATAKA",
    "kerala": "KERALA",
    "maharashtra": "MAHARASHTRA",
    "mp": "MADHYA PRADESH",
    "madhya pradesh": "MADHYA PRADESH",
    "punjab": "PUNJAB",
    "rajasthan": "RAJASTHAN",
    "tamil nadu": "TAMIL NADU",
    "telangana": "TELANGANA",
    "up": "UTTAR PRADESH",
    "uttar pradesh": "UTTAR PRADESH",
    "west bengal": "WEST BENGAL",
}
MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR",
    5: "MAY", 6: "JUN", 7: "JUL", 8: "AUG",
    9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}
DIMENSION_TERMS = {
    "company": ["comname", "coname", "company_name"],
    "companies": ["comname", "coname", "company_name"],
    "state": ["statename", "statecode"],
    "states": ["statename", "statecode"],
    "district": ["distname", "dist"],
    "districts": ["distname", "dist"],
    "sbu": ["sbu_name"],
    "strategic business unit": ["sbu_name"],
    "product": ["productname", "materialgroupname", "prod1"],
    "products": ["productname", "materialgroupname", "prod1"],
    "zone": ["zone_name"],
    "zones": ["zone_name"],
    "region": ["region_name", "ro"],
    "regions": ["region_name", "ro"],
    "month": ["month_name", "yearmonth"],
    "fiscal year": ["fiscal_year", "fiscalyear"],
}


def route_question(
    question: str,
    schema_metadata: Optional[dict] = None,
    model: str = "sqlcoder",
    use_llm_fallback: bool = False,
    llm_confidence_threshold: float = 0.55,
) -> Dict[str, Any]:
    """
    Produce a lightweight routing decision without an LLM by default.

    Existing orchestration only needs the complexity label, but the extra
    deterministic intent signals let later stages avoid repeating cheap
    reasoning work.
    """
    q_norm = _normalize(question)
    schema_tables = _schema_table_names(schema_metadata)

    filters = extract_lightweight_filters(question)
    time_ranges = parse_time_ranges(question)
    metrics = identify_metrics(question)
    aggregations = detect_aggregations(question, metrics=metrics)
    dimensions = infer_routing_dimensions(question)
    source_tables = infer_source_tables(question, schema_tables=schema_tables, metrics=metrics)
    intent = classify_intent(
        question,
        metrics=metrics,
        aggregations=aggregations,
        filters=filters,
        time_ranges=time_ranges,
    )
    deterministic = classify_complexity_hybrid(
        question,
        metrics=metrics,
        aggregations=aggregations,
        filters=filters,
        time_ranges=time_ranges,
        dimensions=dimensions,
        source_tables=source_tables,
        use_llm_fallback=False,
    )

    complexity = deterministic["complexity"]
    routing_mode = "deterministic"
    llm_used = False
    llm_error = None

    if use_llm_fallback and deterministic["confidence"] < llm_confidence_threshold:
        try:
            complexity = classify_complexity_llm(question, model=model)
            routing_mode = "llm_fallback"
            llm_used = True
        except Exception as exc:
            routing_mode = "deterministic_after_llm_error"
            llm_error = str(exc)

    return {
        "routing_version": ROUTING_VERSION,
        "routing_mode": routing_mode,
        "llm_used": llm_used,
        "llm_error": llm_error,
        "intent": intent,
        "complexity": complexity,
        "confidence": deterministic["confidence"],
        "requires_llm_review": deterministic["confidence"] < llm_confidence_threshold,
        "source_tables": source_tables,
        "metrics": metrics,
        "aggregations": aggregations,
        "filters": filters,
        "time_ranges": time_ranges,
        "dimensions": dimensions,
        "signals": deterministic["signals"],
        "is_off_topic": intent == "off_topic",
        "normalized_question": q_norm,
    }


async def route_question_async(
    question: str,
    schema_metadata: Optional[dict] = None,
    model: str = "sqlcoder",
    use_llm_fallback: bool = False,
    llm_confidence_threshold: float = 0.55,
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        route_question,
        question,
        schema_metadata,
        model,
        use_llm_fallback,
        llm_confidence_threshold,
    )


def classify_complexity(
    question: str,
    model: str = "sqlcoder",
    use_llm_fallback: bool = False,
) -> str:
    """Preserved public API: return only the complexity label."""
    return classify_complexity_hybrid(
        question,
        model=model,
        use_llm_fallback=use_llm_fallback,
    )["complexity"]


async def classify_complexity_async(
    question: str,
    model: str = "sqlcoder",
    use_llm_fallback: bool = False,
) -> str:
    return await asyncio.to_thread(classify_complexity, question, model, use_llm_fallback)


def classify_complexity_hybrid(
    question: str,
    model: str = "sqlcoder",
    metrics: Optional[Sequence[dict]] = None,
    aggregations: Optional[Sequence[dict]] = None,
    filters: Optional[Sequence[dict]] = None,
    time_ranges: Optional[Sequence[dict]] = None,
    dimensions: Optional[Sequence[dict]] = None,
    source_tables: Optional[Sequence[dict]] = None,
    use_llm_fallback: bool = False,
) -> Dict[str, Any]:
    deterministic = classify_complexity_deterministic(
        question,
        metrics=metrics,
        aggregations=aggregations,
        filters=filters,
        time_ranges=time_ranges,
        dimensions=dimensions,
        source_tables=source_tables,
    )
    if not use_llm_fallback or deterministic["confidence"] >= 0.55:
        return deterministic

    try:
        label = classify_complexity_llm(question, model=model)
        deterministic.update({"complexity": label, "routing_mode": "llm_fallback", "llm_used": True})
    except Exception as exc:
        deterministic.update({
            "routing_mode": "deterministic_after_llm_error",
            "llm_used": False,
            "llm_error": str(exc),
        })
    return deterministic


def classify_complexity_deterministic(
    question: str,
    metrics: Optional[Sequence[dict]] = None,
    aggregations: Optional[Sequence[dict]] = None,
    filters: Optional[Sequence[dict]] = None,
    time_ranges: Optional[Sequence[dict]] = None,
    dimensions: Optional[Sequence[dict]] = None,
    source_tables: Optional[Sequence[dict]] = None,
) -> Dict[str, Any]:
    q = _normalize(question)
    metrics = list(metrics) if metrics is not None else identify_metrics(question)
    aggregations = list(aggregations) if aggregations is not None else detect_aggregations(question, metrics)
    filters = list(filters) if filters is not None else extract_lightweight_filters(question)
    time_ranges = list(time_ranges) if time_ranges is not None else parse_time_ranges(question)
    dimensions = list(dimensions) if dimensions is not None else infer_routing_dimensions(question)
    source_tables = list(source_tables) if source_tables is not None else infer_source_tables(question, metrics=metrics)

    ultra_signals = [
        "recursive", "hierarchy", "tree", "path", "cumulative over time",
        "pivot", "transpose", "cohort", "retention", "multi-step", "chain",
    ]
    advanced_signals = [
        "rank", "ranking", "top n per", "running total", "moving average",
        "lag", "lead", "previous", "next", "percentile", "window",
        "year over year", "month over month", "time series", "trend",
    ]
    hard_signals = [
        "join", "across", "relate", "combine", "match", "compare",
        "having", "subquery", "nested", "multiple", "all three", "between tables",
    ]

    if any(signal in q for signal in ultra_signals):
        return _complexity_result("ULTRA", 0.92, ["ultra_keyword"])
    if any(signal in q for signal in advanced_signals):
        return _complexity_result("ADVANCED", 0.88, ["advanced_keyword"])
    if len(source_tables) >= 3:
        return _complexity_result("HARD", 0.86, ["three_or_more_tables"])
    if len(source_tables) == 2 or any(signal in q for signal in hard_signals):
        signals = ["join_or_comparison"]
        if aggregations and dimensions:
            signals.append("aggregation_with_join_context")
            return _complexity_result("HARD", 0.82, signals)
        return _complexity_result("HARD", 0.76, signals)

    signals: List[str] = []
    if aggregations:
        signals.append("aggregation")
    if dimensions:
        signals.append("grouping_dimension")
    if any(metric.get("name") == "market_share_percentage" for metric in metrics):
        signals.append("ratio_metric")
    if time_ranges:
        signals.append("time_filter")

    if any(signal in signals for signal in ["aggregation", "grouping_dimension", "ratio_metric"]):
        return _complexity_result("MEDIUM", 0.82 if metrics else 0.72, signals)
    if filters or time_ranges:
        return _complexity_result("SIMPLE", 0.74, ["filters_or_time_lookup"])
    if _is_off_topic(q):
        return _complexity_result("SIMPLE", 0.80, ["off_topic"])
    return _complexity_result("SIMPLE", 0.52, ["default_low_signal"])


def classify_complexity_llm(question: str, model: str = "sqlcoder", timeout: int = 30) -> str:
    """Optional LLM classifier retained for migration fallback."""
    payload = {
        "model": model,
        "prompt": question,
        "system": SYSTEM_PROMPT_PHASE5,
        "stream": False,
        "keep_alive": -1,
        "options": {"temperature": 0.0, "num_predict": 12},
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    raw = resp.json().get("response", "").strip().upper()
    for label in VALID_COMPLEXITIES:
        if re.search(rf"\b{label}\b", raw):
            return label
    raise ValueError(f"LLM returned invalid complexity label: {raw!r}")


def classify_complexity_heuristic(question: str) -> str:
    """Rule-based fallback classifier (no LLM needed)."""
    return classify_complexity_deterministic(question)["complexity"]


def classify_intent(
    question: str,
    metrics: Optional[Sequence[dict]] = None,
    aggregations: Optional[Sequence[dict]] = None,
    filters: Optional[Sequence[dict]] = None,
    time_ranges: Optional[Sequence[dict]] = None,
) -> str:
    q = _normalize(question)
    metrics = list(metrics) if metrics is not None else identify_metrics(question)
    aggregations = list(aggregations) if aggregations is not None else detect_aggregations(question, metrics)
    filters = list(filters or [])
    time_ranges = list(time_ranges or [])

    if _is_off_topic(q):
        return "off_topic"
    if any(term in q for term in ["table", "columns", "schema", "records", "rows", "show", "list"]) and not metrics:
        return "schema_or_record_lookup"
    if any(term in q for term in ["trend", "month over month", "year over year", "growth", "difference", "running", "previous", "next"]):
        return "trend_analysis"
    if any(term in q for term in ["rank", "ranking", "highest", "lowest", "top", "bottom", "compare"]):
        return "comparison_or_ranking"
    if metrics or aggregations:
        return "metric_query"
    if filters or time_ranges:
        return "filtered_lookup"
    return "general_lookup"


def identify_metrics(question: str) -> List[Dict[str, Any]]:
    q = _normalize(question)
    metrics: List[Dict[str, Any]] = []

    if "market share" in q or "market contribution" in q:
        metrics.append({
            "name": "market_share_percentage",
            "type": "ratio",
            "candidate_columns": ["netweight_tmt", "total"],
            "aggregation": "ratio",
            "confidence": 0.94,
        })
        return metrics

    if "count" in q or "how many" in q or "number of" in q:
        metrics.append({
            "name": "row_count",
            "type": "count",
            "candidate_columns": ["*"],
            "aggregation": "count",
            "confidence": 0.88,
        })
        return metrics

    if "target" in q:
        preferred = "target_qty_kl" if re.search(r"\bkl\b", q) else "target_qty_tmt"
        metrics.append({
            "name": "target_quantity",
            "type": "measure",
            "candidate_columns": [preferred, "target_qty_tmt", "target_quantity_tmtt", "target_round"],
            "aggregation": _infer_aggregate_function(q) or "sum",
            "confidence": 0.90,
        })

    if any(term in q for term in ["sales", "actual", "netweight", "quantity", "volume", "performance"]):
        metrics.append({
            "name": "actual_sales",
            "type": "measure",
            "candidate_columns": ["netweight_tmt", "actual_round", "total"],
            "aggregation": _infer_aggregate_function(q) or "sum",
            "confidence": 0.86,
        })

    return _dedupe_named_dicts(metrics, "name")


def detect_aggregations(question: str, metrics: Optional[Sequence[dict]] = None) -> List[Dict[str, Any]]:
    q = _normalize(question)
    aggregations: List[Dict[str, Any]] = []
    metrics = list(metrics or [])

    has_ratio_metric = any(metric.get("aggregation") == "ratio" for metric in metrics)
    function = _infer_aggregate_function(q)
    if function and not has_ratio_metric:
        aggregations.append({"function": function, "source": "keyword", "confidence": 0.86})

    for metric in metrics:
        aggregation = metric.get("aggregation")
        if not aggregation:
            continue
        if aggregation == "ratio":
            aggregations.append({"function": "ratio", "source": f"metric:{metric.get('name')}", "confidence": metric.get("confidence", 0.75)})
        else:
            aggregations.append({"function": aggregation, "source": f"metric:{metric.get('name')}", "confidence": metric.get("confidence", 0.75)})

    if infer_routing_dimensions(question):
        aggregations.append({"function": "group_by", "source": "dimension_keyword", "confidence": 0.78})

    return _dedupe_named_dicts(aggregations, "function")


def extract_lightweight_filters(question: str) -> List[Dict[str, Any]]:
    q = _normalize(question)
    filters: List[Dict[str, Any]] = []

    for company in COMPANY_NAMES:
        if re.search(rf"\b{re.escape(company.lower())}\b", q):
            filters.append({"type": "company", "column_candidates": ["comname", "coname", "company_name"], "operator": "=", "value": company})

    for product in PRODUCT_NAMES:
        if re.search(rf"\b{re.escape(product.lower())}\b", q) and not _labeled_context(q, product.lower(), "sbu"):
            filters.append({"type": "product", "column_candidates": ["productname", "materialgroupname", "prod1"], "operator": "=", "value": product})

    for phrase, value in SBU_VALUES.items():
        if _contains_phrase(q, phrase) and not _labeled_context(q, phrase, "product"):
            filters.append({"type": "sbu", "column_candidates": ["sbu_name"], "operator": "=", "value": value})

    for alias, state in sorted(STATE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", q):
            filters.append({"type": "state", "column_candidates": ["statename", "statecode"], "operator": "=", "value": state})
            break

    zone = _extract_zone(q)
    if zone:
        filters.append({"type": "zone", "column_candidates": ["zone_name"], "operator": "=", "value": zone})

    district = _extract_named_entity_after(q, "district")
    if district:
        filters.append({"type": "district", "column_candidates": ["distname", "dist"], "operator": "=", "value": district.upper()})

    return _dedupe_filter_dicts(filters)


def parse_time_ranges(question: str) -> List[Dict[str, Any]]:
    q = _normalize(question)
    ranges: List[Dict[str, Any]] = []

    fiscal_years = _extract_fiscal_years(q)
    if len(fiscal_years) >= 2:
        ranges.append({
            "type": "fiscal_year_range",
            "column_candidates": ["fiscal_year", "fiscalyear"],
            "operator": "in",
            "value": fiscal_years[:2],
            "dynamic": False,
        })
    elif fiscal_years:
        ranges.append({
            "type": "fiscal_year",
            "column_candidates": ["fiscal_year", "fiscalyear"],
            "operator": "=",
            "value": fiscal_years[0],
            "dynamic": False,
        })
    elif _mentions_current_fiscal_year(q):
        ranges.append({
            "type": "fiscal_year",
            "column_candidates": ["fiscal_year", "fiscalyear"],
            "operator": "=",
            "value": CURRENT_FINANCIAL_YEAR,
            "dynamic": False,
        })
    elif _mentions_previous_fiscal_year(q):
        ranges.append({
            "type": "fiscal_year",
            "column_candidates": ["fiscal_year", "fiscalyear"],
            "operator": "=",
            "value": _previous_fiscal_year(),
            "dynamic": False,
        })

    date_range = _extract_date_range(q)
    if date_range:
        ranges.append({
            "type": "date_range",
            "column_candidates": ["day_id", "created_at", "updated_at"],
            "operator": "between",
            "value": date_range,
            "dynamic": False,
        })
        return ranges

    month_year = _extract_month_year(q)
    if month_year:
        ranges.append({
            "type": "month",
            "column_candidates": ["yearmonth", "month_name"],
            "operator": "=",
            "value": month_year,
            "dynamic": False,
        })

    if "current month" in q or "this month" in q:
        ranges.append({
            "type": "month",
            "column_candidates": ["yearmonth", "month_name", "day_id"],
            "operator": "=",
            "value": {"relative": "current"},
            "dynamic": True,
        })
    elif "last month" in q or "previous month" in q:
        ranges.append({
            "type": "month",
            "column_candidates": ["yearmonth", "month_name", "day_id"],
            "operator": "=",
            "value": {"relative": "previous"},
            "dynamic": True,
        })

    return ranges


def infer_routing_dimensions(question: str) -> List[Dict[str, Any]]:
    q = _normalize(question)
    dimensions: List[Dict[str, Any]] = []
    group_signal = bool(re.search(r"\b(by|per|grouped by|breakdown by|rank|ranking)\b", q))
    group_signal = group_signal or bool(re.search(r"\b(?:top|bottom|first|last)\s+\d{1,3}\b", q))
    group_signal = group_signal or bool(re.search(r"\bwhich\s+(company|companies|state|district|sbu|product|zone|region|month)\b", q))
    if not group_signal:
        return dimensions

    for term, columns in DIMENSION_TERMS.items():
        if term in {"sbu", "strategic business unit"} and _all_sbus_requested(q):
            continue
        if _contains_phrase(q, term):
            dimensions.append({"source": term, "column_candidates": columns, "confidence": 0.80})

    return _dedupe_named_dicts(dimensions, "source")


def infer_source_tables(
    question: str,
    schema_tables: Optional[Sequence[str]] = None,
    metrics: Optional[Sequence[dict]] = None,
) -> List[Dict[str, Any]]:
    q = _normalize(question)
    known = set(schema_tables or [])
    tables: List[Dict[str, Any]] = []
    company_signal = any(
        re.search(rf"\b{re.escape(company.lower())}\b", q)
        for company in COMPANY_NAMES
    )
    comparison_signal = _has_sales_comparison_intent(q) and not _has_target_actual_comparison_intent(q)

    def add(table: str, reason: str, confidence: float) -> None:
        if known and table not in known:
            return
        if not any(item["table"] == table for item in tables):
            tables.append({"table": table, "reason": reason, "confidence": confidence})

    for table in known:
        if _contains_phrase(q, table) or _contains_phrase(q, table.replace("_", " ")):
            add(table, "explicit_table", 0.95)

    if "industry" in q or "market share" in q or "market contribution" in q or company_signal or comparison_signal:
        add("industry_performance", "industry_or_company_keyword", 0.88)
    if "m60" in q or "target" in q:
        add("m60_level", "target_keyword", 0.84)
    if "mom" in q or "actual" in q or "sales" in q or "date" in q or "day" in q:
        if "market share" not in q and "market contribution" not in q and "industry" not in q and not company_signal and not comparison_signal:
            add("mom_level", "actual_or_sales_keyword", 0.82)

    for metric in metrics or []:
        if metric.get("name") == "market_share_percentage":
            add("industry_performance", "metric_preference", 0.90)
        elif metric.get("name") == "target_quantity":
            add("m60_level", "metric_preference", 0.86)
        elif metric.get("name") == "actual_sales" and not tables:
            add("mom_level", "metric_preference", 0.80)

    return tables


def _complexity_result(complexity: str, confidence: float, signals: Sequence[str]) -> Dict[str, Any]:
    return {
        "complexity": complexity,
        "confidence": round(max(0.0, min(confidence, 1.0)), 4),
        "signals": list(signals),
        "routing_mode": "deterministic",
        "llm_used": False,
    }


def _schema_table_names(schema_metadata: Optional[dict]) -> List[str]:
    if not schema_metadata:
        return []
    tables = []
    for table in schema_metadata.get("tables", []) or []:
        table_name = table.get("table_name")
        if table_name:
            tables.append(str(table_name).lower())
    return tables


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _is_off_topic(q_norm: str) -> bool:
    if not q_norm:
        return True
    greetings = {"hi", "hello", "hey", "how are you", "who are you", "what are you"}
    if q_norm in greetings:
        return True
    general_sql = ["what is sql", "explain sql", "teach me sql", "what is database"]
    if any(term in q_norm for term in general_sql):
        return True
    business_terms = [
        "sales", "target", "actual", "industry", "performance", "market share",
        "sbu", "product", "company", "fiscal", "month", "zone", "region",
        "district", "state", "m60", "mom", "table", "records", "rows",
        "columns", "data", "query", "netweight", "quantity", "volume",
    ]
    return not any(term in q_norm for term in business_terms)


def _contains_phrase(q_norm: str, phrase: str) -> bool:
    phrase_norm = _normalize(phrase)
    if not phrase_norm:
        return False
    if len(phrase_norm) <= 3:
        return bool(re.search(rf"\b{re.escape(phrase_norm)}\b", q_norm))
    return phrase_norm in q_norm


def _labeled_context(q_norm: str, value: str, label: str) -> bool:
    return bool(
        re.search(rf"\b{re.escape(value)}\b\s+{label}\b", q_norm)
        or re.search(rf"\b{label}\b\s+(?:is\s+)?\b{re.escape(value)}\b", q_norm)
    )


def _infer_aggregate_function(q_norm: str) -> Optional[str]:
    if any(term in q_norm for term in ["average", "avg", "mean"]):
        return "avg"
    if any(term in q_norm for term in ["maximum", "max"]):
        return "max"
    if any(term in q_norm for term in ["minimum", "min"]):
        return "min"
    if any(term in q_norm for term in ["count", "how many", "number of"]):
        return "count"
    if any(term in q_norm for term in ["total", "sum", "sales", "quantity", "actual", "target", "rank", "highest", "lowest"]):
        return "sum"
    return None


def _dedupe_named_dicts(items: Sequence[dict], key: str) -> List[dict]:
    seen = set()
    deduped = []
    for item in items:
        value = item.get(key)
        if value in seen:
            continue
        seen.add(value)
        deduped.append(item)
    return deduped


def _dedupe_filter_dicts(filters: Sequence[dict]) -> List[dict]:
    seen = set()
    deduped = []
    for item in filters:
        key = (item.get("type"), item.get("operator"), str(item.get("value")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _extract_zone(q_norm: str) -> Optional[str]:
    if "zone" not in q_norm:
        return None

    # Check specific long zone patterns first
    zone_mappings = {
        "south western": "South Western Zone",
        "southwest": "South Western Zone",
        "north frontier": "North Frontier Zone",
        "east central": "East Central Zone",
        "central": "Central Zone",
        "north central retail": "North Central Retail",
        "south central retail": "South Central Retail",
        "south central lpg": "South Central LPG Zo",
        "north central lpg": "North Central LPG Zo",
        "north west retail": "North West Retail Zo",
        "north west frontier": "North West Frontier",
        "north west lpg": "North West LPG Zone",
        "north west": "North West",
    }
    
    for key, val in sorted(zone_mappings.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", q_norm):
            return val

    for zone in ["north", "south", "east", "west", "central"]:
        if re.search(rf"\b{zone}\b", q_norm):
            return zone.title()

    zone_match = re.search(r"\b([a-z]{1,4}z)\s+zone\b", q_norm)
    if zone_match:
        return zone_match.group(1).upper()

    return None


def _has_sales_comparison_intent(q_norm: str) -> bool:
    return any(term in q_norm for term in [
        "growth", "difference", "compare", "against", "year over year", "year-on-year", "yoy",
    ])


def _has_target_actual_comparison_intent(q_norm: str) -> bool:
    return "target" in q_norm and "actual" in q_norm


def _extract_named_entity_after(q_norm: str, label: str) -> Optional[str]:
    match = re.search(
        rf"\b{re.escape(label)}\s+([a-z0-9&.\- ]+?)(?=\s+(?:for|from|in|on|by|with|and|between|during|current|this|last|top|bottom)\b|$)",
        q_norm,
    )
    if not match:
        return None
    value = match.group(1).strip(" .,-")
    return value or None


def _extract_fiscal_year(q_norm: str) -> Optional[str]:
    fiscal_years = _extract_fiscal_years(q_norm)
    return fiscal_years[0] if fiscal_years else None


def _extract_fiscal_years(q_norm: str) -> List[str]:
    years: List[str] = []
    for match in re.finditer(r"\b(?:fy\s*)?(20\d{2})\s*[-/]\s*(20\d{2})\b", q_norm):
        fiscal_year = f"{match.group(1)}-{match.group(2)}"
        if fiscal_year not in years:
            years.append(fiscal_year)
    return years


def _mentions_current_fiscal_year(q_norm: str) -> bool:
    return any(term in q_norm for term in [
        "current fiscal year", "current financial year", "this fiscal year",
        "this financial year", "current fy", "this fy",
    ])


def _mentions_previous_fiscal_year(q_norm: str) -> bool:
    return bool(re.search(
        r"\b(?:ly|last year|previous fiscal year|last fiscal year|previous financial year|last financial year)\b",
        q_norm,
    ))


def _previous_fiscal_year() -> str:
    start_year = int(CURRENT_FINANCIAL_YEAR.split("-", 1)[0])
    return f"{start_year - 1}-{start_year}"


def _all_sbus_requested(q_norm: str) -> bool:
    return bool(re.search(r"\ball\s+(?:sbu|sbus|sbu's|strategic business units)\b", q_norm))


def _extract_date_range(q_norm: str) -> Optional[Dict[str, str]]:
    matches = list(re.finditer(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b",
        q_norm,
    ))
    if not matches:
        return None

    parsed_dates = []
    for match in matches[:2]:
        month = MONTHS[match.group(1)]
        day = int(match.group(2))
        year = int(match.group(3))
        try:
            parsed_dates.append(date(year, month, day).isoformat())
        except ValueError:
            continue

    if len(parsed_dates) == 1:
        return {"start": parsed_dates[0], "end": parsed_dates[0]}
    if len(parsed_dates) == 2:
        start, end = sorted(parsed_dates)
        return {"start": start, "end": end}
    return None


def _extract_month_year(q_norm: str) -> Optional[Dict[str, Any]]:
    match = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(20\d{2})\b",
        q_norm,
    )
    if not match:
        return None
    month = MONTHS[match.group(1)]
    year = int(match.group(2))
    return {"year": year, "month": month, "yearmonth": year * 100 + month, "month_name": MONTH_ABBR[month]}


def get_complexity_system_prompt(complexity: str) -> str:
    return COMPLEXITY_PROMPT_TEMPLATES.get(complexity, COMPLEXITY_PROMPT_TEMPLATES["MEDIUM"])


def get_complexity_color(complexity: str) -> str:
    colors = {
        "SIMPLE": "#22c55e",
        "MEDIUM": "#3b82f6",
        "HARD": "#f59e0b",
        "ADVANCED": "#f97316",
        "ULTRA": "#ef4444"
    }
    return colors.get(complexity, "#6b7280")


def get_complexity_badge(complexity: str) -> str:
    icons = {
        "SIMPLE": "🟢",
        "MEDIUM": "🔵",
        "HARD": "🟡",
        "ADVANCED": "🟠",
        "ULTRA": "🔴"
    }
    return f"{icons.get(complexity, '⚪')} {complexity}"
