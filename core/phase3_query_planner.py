"""
NLPSQL SYSTEM - PHASE 03A
Logical Query Planner

This module introduces a migration-safe intermediate representation between
natural language and SQL. It does not execute SQL and does not mutate state.
"""

import json
import re
from datetime import date
from typing import Any, Dict, List, Optional
from config import CURRENT_FINANCIAL_YEAR
from core.phase3_relationship_graph import enrich_logical_plan_with_joins


PLAN_VERSION = "logical-query-plan-v1"

COMPANY_NAMES = [
    "HPCL", "IOCL", "BPCL", "SHELL", "GAIL", "CPCL", "HMEL", "MRPL",
    "NEL", "NRL", "RIL", "RELIANCE", "NAYARA", "ONGC", "SMA", "OIL",
]

PRODUCT_NAMES = [
    "HSD", "MS", "LPG", "PROPANE", "NAPHTHA", "SKO", "ATF", "LDO", "FO"
]

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
    "months": ["month_name", "yearmonth"],
    "fiscal year": ["fiscal_year", "fiscalyear"],
}


def generate_logical_plan(
    question: str,
    schema_context: Optional[List[dict]] = None,
    schema_metadata: Optional[dict] = None,
    entity_matches: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """
    Build a structured, JSON-serializable logical query plan.

    The extraction is intentionally deterministic. The LLM still handles final
    SQL rendering, while this stage makes table, metric, filter, aggregation,
    and group-by intent explicit and inspectable.
    """
    schema_context = schema_context or []
    schema_index = _build_schema_index(schema_metadata)
    q_norm = _normalize(question)

    plan = _empty_plan(question)
    if _is_off_topic(q_norm):
        plan["is_off_topic"] = True
        plan["notes"].append("Question appears unrelated to the loaded business schema.")
        plan["confidence"] = 0.95
        return plan

    tables = _infer_tables(q_norm, schema_context, schema_index, entity_matches, question=question)
    plan["source_tables"] = tables
    primary_table = tables[0]["table"] if tables else None

    plan["metrics"] = extract_metrics(q_norm, primary_table, schema_index)
    plan["filters"] = extract_filters(q_norm, primary_table, schema_index, entity_matches, question=question)
    plan["aggregations"] = infer_aggregations(q_norm, plan["metrics"])
    plan["group_by"] = infer_group_by(q_norm, primary_table, schema_index, plan["filters"], plan["aggregations"])
    plan["order_by"] = infer_ordering(q_norm, plan["metrics"], plan["group_by"])
    plan["projection"] = infer_projection(q_norm, plan["metrics"], plan["group_by"])
    plan["limit"] = infer_limit(q_norm)
    plan = enrich_logical_plan_with_joins(plan, schema_metadata, schema_context)
    plan["confidence"] = _score_confidence(plan)

    if not plan["source_tables"]:
        plan["notes"].append("No source table could be inferred from schema context or keywords.")
    if not plan["metrics"] and not plan["projection"]:
        plan["notes"].append("No metric or projection could be inferred.")

    return plan


def generate_logical_plan_json(
    question: str,
    schema_context: Optional[List[dict]] = None,
    schema_metadata: Optional[dict] = None,
) -> str:
    """Return the logical plan as pretty JSON for prompts, logs, or tests."""
    return json.dumps(
        generate_logical_plan(question, schema_context, schema_metadata),
        indent=2,
        sort_keys=True,
    )


def extract_metrics(q_norm: str, table: Optional[str], schema_index: Dict[str, Any]) -> List[Dict[str, Any]]:
    metrics: List[Dict[str, Any]] = []
    columns = _columns_for(table, schema_index)
    unit = _infer_unit(q_norm)

    if any(term in q_norm for term in ["market share", "market contribution", "contributed", "contribution"]):
        base_column = _first_existing(columns, ["netweight_tmt", "total"])
        metrics.append({
            "name": "market_share_percentage",
            "type": "ratio",
            "base_measure": {"column": base_column, "aggregation": "sum", "unit": "TMT"},
            "denominator_scope": "all_matching_market_rows",
            "unit": "percentage",
            "alias": "market_share_percentage",
        })
        return metrics

    aggregate = _infer_aggregate_function(q_norm)

    if "count" in q_norm or "how many" in q_norm or "number of" in q_norm:
        metrics.append({
            "name": "row_count",
            "type": "count",
            "column": "*",
            "aggregation": "count",
            "alias": "row_count",
        })
        return metrics

    if "target" in q_norm:
        preferred = "target_qty_kl" if "kl" in q_norm else "target_qty_tmt"
        column = _first_existing(columns, [preferred, "target_qty_tmt", "target_quantity_tmtt", "target_round"])
        if column:
            metrics.append({
                "name": "target_quantity",
                "type": "measure",
                "column": column,
                "aggregation": aggregate or "sum",
                "unit": "KL" if column.endswith("_kl") else "TMT",
                "alias": _metric_alias("target", column, aggregate or "sum"),
            })
            return metrics

    sales_terms = ["sales", "actual", "netweight", "quantity", "performance", "volume"]
    if any(term in q_norm for term in sales_terms) or _has_ranking_intent(q_norm):
        column = _first_existing(columns, ["netweight_tmt", "actual_round", "total"])
        if column:
            metric: Dict[str, Any] = {
                "name": "actual_sales",
                "type": "measure",
                "column": column,
                "aggregation": aggregate or "sum",
                "unit": "TMT",
                "alias": _metric_alias("actual_sales", column, aggregate or "sum"),
            }
            if unit == "MT" and column == "netweight_tmt":
                metric["conversion"] = {"operation": "multiply", "factor": 1000, "from_unit": "TMT", "to_unit": "MT"}
                metric["unit"] = "MT"
                metric["alias"] = metric["alias"].replace("_tmt", "_mt")
            metrics.append(metric)

    return metrics


def extract_filters(
    q_norm: str,
    table: Optional[str],
    schema_index: Dict[str, Any],
    entity_matches: Optional[List[dict]] = None,
    question: Optional[str] = None,
) -> List[Dict[str, Any]]:
    filters: List[Dict[str, Any]] = []
    columns = _columns_for(table, schema_index)
    catalog_products = [
        str(item.get("value"))
        for item in entity_matches or []
        if item.get("type") == "product" and item.get("value")
    ]
    catalog_zones = [
        str(item.get("value"))
        for item in entity_matches or []
        if item.get("type") == "zone" and item.get("value")
    ]

    if table == "industry_performance" and "category" in columns:
        filters.append(_filter(table, "category", "not_equal_or_null", "O", "business_rule", True))

    if table == "mom_level" and "sbu_name" in columns:
        filters.append(_filter(table, "sbu_name", "not_equal", "0", "business_rule", True))
        filters.append(_filter(
            table,
            "sbu_name",
            "not_in",
            ["Common", "Mumbai Ref", "Renewable Energy", "Visakh Ref"],
            "business_rule",
            True,
        ))

    explicit_sbu = None
    if question:
        explicit_sbu = _extract_explicit_sbu(q_norm, question)

    if explicit_sbu and "sbu_name" in columns:
        sbu_value = explicit_sbu.upper() if table == "industry_performance" else explicit_sbu
        filters.append(_filter(table, "sbu_name", "equal", sbu_value, "sbu", False))

    q_company_norm = re.sub(r"\b(?:strategic\s+business\s+unit|sbu)\s+hpcl\s+mkt(?:\s+ds)?\b", " ", q_norm)
    for company in COMPANY_NAMES:
        if explicit_sbu and company.lower() in explicit_sbu.lower():
            continue
        if re.search(rf"\b{re.escape(company.lower())}\b", q_company_norm):
            company_col = _first_existing(columns, ["comname", "coname", "company_name"])
            if company_col:
                filters.append(_filter(table, company_col, "equal", company, "company_name", False))

    for product in PRODUCT_NAMES:
        if explicit_sbu and product.lower() in explicit_sbu.lower():
            continue
        if (
            re.search(rf"\b{re.escape(product.lower())}\b", q_norm)
            and not _labeled_context(q_norm, product.lower(), "sbu")
            and not _covered_by_longer_value(product, catalog_products)
            and not _covered_by_longer_value(product, catalog_zones)
        ):
            product_col = _first_existing(columns, ["productname", "materialgroupname", "prod1"])
            if product_col:
                filters.append(_filter(table, product_col, "equal", product, "product", False))

    if not explicit_sbu:
        for phrase, value in SBU_VALUES.items():
            if (
                _contains_phrase(q_norm, phrase)
                and not _labeled_context(q_norm, phrase, "product")
                and not _covered_by_longer_value(phrase, catalog_products)
                and not _covered_by_longer_value(phrase, catalog_zones)
            ):
                if "sbu_name" in columns:
                    sbu_value = value.upper() if table == "industry_performance" else value
                    filters.append(_filter(table, "sbu_name", "equal", sbu_value, "sbu", False))

    for entity in entity_matches or []:
        entity_type = entity.get("type")
        if entity_type == "company":
            if explicit_sbu and str(entity.get("value")).lower() in explicit_sbu.lower():
                continue
            company_col = _first_existing(columns, ["comname", "coname", "company_name"])
            if company_col:
                filters.append(_filter(table, company_col, "equal", entity.get("value"), "database_entity_catalog", False))
        elif entity_type == "sbu" and "sbu_name" in columns:
            if explicit_sbu:
                continue
            value = entity.get("value")
            filters.append(_filter(
                table,
                "sbu_name",
                "equal",
                value.upper() if table == "industry_performance" and isinstance(value, str) else value,
                "database_entity_catalog",
                False,
            ))
        elif entity_type == "product":
            if explicit_sbu and str(entity.get("value")).lower() in explicit_sbu.lower():
                continue
            product_col = _first_existing(columns, ["productname", "materialgroupname", "prod1"])
            if product_col:
                filters.append(_filter(table, product_col, "equal", entity.get("value"), "database_entity_catalog", False))
        elif entity_type == "state":
            state_col = _first_existing(columns, ["statename", "statecode"])
            if state_col:
                filters.append(_filter(table, state_col, "equal", entity.get("value"), "database_entity_catalog", False))
        elif entity_type == "district":
            district_col = _first_existing(columns, ["distname", "dist"])
            if district_col:
                filters.append(_filter(table, district_col, "equal", entity.get("value"), "database_entity_catalog", False))
        elif entity_type == "region":
            region_col = _first_existing(columns, ["region_name", "ro", "region"])
            if region_col:
                filters.append(_filter(table, region_col, "equal", entity.get("value"), "database_entity_catalog", False))
        elif entity_type == "zone" and "zone_name" in columns:
            value = entity.get("value")
            value = _map_zone_to_base_if_needed(value, q_norm, table)
            is_growth = any(term in q_norm for term in ["growth", "difference", "year over year", "year-on-year", "yoy"])
            zone_val = (_industry_zone_value(value) if is_growth else value) if table == "industry_performance" else value
            filters.append(_filter(
                table,
                "zone_name",
                "equal",
                zone_val,
                "database_entity_catalog",
                False,
            ))

    state_value = _extract_state(q_norm)
    if state_value:
        state_col = _first_existing(columns, ["statename", "statecode"])
        if state_col:
            filters.append(_filter(table, state_col, "equal", state_value, "state", False))

    district = _extract_named_entity_after(q_norm, "district")
    if district:
        district_col = _first_existing(columns, ["distname", "dist"])
        if district_col:
            filters.append(_filter(table, district_col, "equal", district.upper(), "district", False))

    zone = _extract_zone(q_norm)
    if zone and "zone_name" in columns and not _covered_by_longer_value(zone, catalog_zones):
        zone = _map_zone_to_base_if_needed(zone, q_norm, table)
        is_growth = any(term in q_norm for term in ["growth", "difference", "year over year", "year-on-year", "yoy"])
        zone_val = (_industry_zone_value(zone) if is_growth else zone) if table == "industry_performance" else zone
        filters.append(_filter(
            table,
            "zone_name",
            "equal",
            zone_val,
            "zone",
            False,
        ))

    fiscal_years = _extract_fiscal_years(q_norm)
    fiscal_col = _first_existing(columns, ["fiscal_year", "fiscalyear"])
    if len(fiscal_years) >= 2 and fiscal_col:
        filters.append(_filter(table, fiscal_col, "in", fiscal_years[:2], "fiscal_year_range", False))
    elif fiscal_years and fiscal_col:
        filters.append(_filter(table, fiscal_col, "equal", fiscal_years[0], "fiscal_year", False))
    elif _mentions_current_fiscal_year(q_norm) and fiscal_col:
        filters.append(_filter(
            table,
            fiscal_col,
            "equal",
            CURRENT_FINANCIAL_YEAR,
            "current_fiscal_year",
            False,
        ))
    elif _mentions_previous_fiscal_year(q_norm) and fiscal_col:
        filters.append(_filter(
            table,
            fiscal_col,
            "equal",
            _previous_fiscal_year(),
            "previous_fiscal_year",
            False,
        ))

    date_range = _extract_date_range(q_norm)
    if date_range and "day_id" in columns:
        filters.append(_filter(table, "day_id", "between", date_range, "date_range", False))
    else:
        month_year = _extract_month_year(q_norm)
        if month_year:
            if "yearmonth" in columns:
                filters.append(_filter(table, "yearmonth", "equal", month_year["yearmonth"], "month_year", False))
            elif "month_name" in columns:
                filters.append(_filter(table, "month_name", "equal", month_year["month_name"], "month", False))

    return _merge_duplicate_filters(_dedupe_filters(filters))


def infer_aggregations(q_norm: str, metrics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    aggregations: List[Dict[str, Any]] = []
    for metric in metrics:
        if metric.get("type") == "ratio":
            aggregations.append({
                "type": "ratio",
                "name": metric["name"],
                "numerator": metric["base_measure"],
                "denominator": {
                    "column": metric["base_measure"].get("column"),
                    "aggregation": metric["base_measure"].get("aggregation"),
                    "scope": metric["denominator_scope"],
                },
                "alias": metric["alias"],
            })
            continue

        aggregation = metric.get("aggregation")
        if aggregation:
            aggregations.append({
                "function": aggregation,
                "column": metric.get("column"),
                "alias": metric.get("alias"),
            })

    return aggregations


def infer_group_by(
    q_norm: str,
    table: Optional[str],
    schema_index: Dict[str, Any],
    filters: List[Dict[str, Any]],
    aggregations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not aggregations:
        return []

    columns = _columns_for(table, schema_index)
    filtered_columns = {item["column"] for item in filters if item.get("operator") in {"equal", "equal_dynamic"}}
    dimensions: List[Dict[str, Any]] = []

    group_signal = bool(re.search(r"\b(by|per|grouped by|breakdown by|rank|ranking)\b", q_norm))
    group_signal = group_signal or bool(re.search(r"\b(?:top|bottom|first|last)\s+\d{1,3}\b", q_norm))
    group_signal = group_signal or bool(re.search(r"\bwhich\s+(company|companies|state|district|sbu|product|zone|region|month)\b", q_norm))
    group_signal = group_signal or bool(re.search(r"\b(?:highest|lowest|best|worst)\b", q_norm))
    has_plural_dimension = any(
        term in q_norm
        for term in ["companies", "zones", "regions", "sbus", "states", "districts", "products", "months"]
    )
    group_signal = group_signal or has_plural_dimension
    is_comparison = bool(re.search(r"\b(compare|comparison|versus|vs|against)\b", q_norm))
    is_growth = any(term in q_norm for term in ["growth", "difference", "year over year", "year-on-year", "yoy"])
    if is_comparison:
        group_signal = True

    if not group_signal:
        return []

    if is_comparison:
        for f in filters:
            if f.get("operator") == "in" and not f.get("is_business_rule"):
                col = f.get("column")
                if is_growth and col in ("fiscal_year", "yearmonth", "month_name"):
                    continue
                source_term = "comparison_dimension"
                for term, candidates in DIMENSION_TERMS.items():
                    if col in candidates:
                        source_term = term
                        break
                dimensions.append({"table": table, "column": col, "source": source_term})

    for term, candidates in DIMENSION_TERMS.items():
        if term in {"sbu", "strategic business unit"} and _all_sbus_requested(q_norm):
            continue
        if _contains_phrase(q_norm, term):
            column = _first_existing(columns, candidates)
            if column and column not in filtered_columns:
                dimensions.append({"table": table, "column": column, "source": term})

    if _has_ranking_intent(q_norm) and _contains_phrase(q_norm, "company"):
        company_col = _first_existing(columns, ["comname", "coname", "company_name"])
        if company_col and company_col not in filtered_columns:
            dimensions.append({"table": table, "column": company_col, "source": "ranking_dimension"})

    return _dedupe_dimensions(dimensions)


def infer_ordering(
    q_norm: str,
    metrics: List[Dict[str, Any]],
    group_by: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not metrics:
        return []

    metric_name = metrics[0].get("alias") or metrics[0].get("name")
    if any(term in q_norm for term in ["lowest", "least", "bottom", "minimum", "min", "worst"]):
        return [{"field": metric_name, "direction": "asc", "reason": "lowest_requested"}]
    if any(term in q_norm for term in ["highest", "largest", "maximum", "max", "top", "rank", "ranking", "best"]):
        return [{"field": metric_name, "direction": "desc", "reason": "highest_or_ranking_requested"}]
    if group_by and "rank" in q_norm:
        return [{"field": metric_name, "direction": "desc", "reason": "ranking_requested"}]
    return []


def infer_projection(
    q_norm: str,
    metrics: List[Dict[str, Any]],
    group_by: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if metrics or group_by:
        projection = [{"type": "dimension", **dim} for dim in group_by]
        projection.extend({"type": "metric", "name": item.get("name"), "alias": item.get("alias")} for item in metrics)
        return projection

    if any(term in q_norm for term in ["show", "list", "all records", "records", "details"]):
        return [{"type": "wildcard", "value": "*"}]

    return []


def infer_limit(q_norm: str) -> Optional[int]:
    top_match = re.search(
        r"\b(?:top|bottom|highest|lowest|largest|smallest|best|worst|first|last)\s+(\d{1,3})\b",
        q_norm,
    )
    if top_match:
        return int(top_match.group(1))
    if any(term in q_norm for term in ["highest", "lowest", "largest", "smallest", "best", "worst"]):
        return 1
    return None


def _empty_plan(question: str) -> Dict[str, Any]:
    return {
        "plan_version": PLAN_VERSION,
        "question": question,
        "is_off_topic": False,
        "source_tables": [],
        "metrics": [],
        "filters": [],
        "aggregations": [],
        "group_by": [],
        "order_by": [],
        "projection": [],
        "limit": None,
        "joins": [],
        "join_paths": [],
        "join_confidence": 1.0,
        "join_strategy": "single_table",
        "notes": [],
        "confidence": 0.0,
    }


def _build_schema_index(schema_metadata: Optional[dict]) -> Dict[str, Any]:
    index: Dict[str, Any] = {"tables": {}}
    if not schema_metadata:
        return index

    for table in schema_metadata.get("tables", []):
        table_name = table.get("table_name")
        if not table_name:
            continue
        columns = {}
        for col in table.get("columns", []):
            clean_name = col.get("clean_name")
            if clean_name:
                columns[clean_name.lower()] = col
        index["tables"][table_name.lower()] = {"raw": table, "columns": columns}
    return index


def _infer_tables(
    q_norm: str,
    schema_context: List[dict],
    schema_index: Dict[str, Any],
    entity_matches: Optional[List[dict]] = None,
    question: Optional[str] = None,
) -> List[Dict[str, Any]]:
    explicit: List[Dict[str, Any]] = []
    known_tables = schema_index.get("tables", {})

    q_company_norm = re.sub(r"\b(?:strategic\s+business\s+unit|sbu)\s+hpcl\s+mkt(?:\s+ds)?\b", " ", q_norm)
    company_signal = any(
        re.search(rf"\b{re.escape(company.lower())}\b", q_company_norm)
        for company in COMPANY_NAMES
    )
    company_signal = company_signal or any(item.get("type") == "company" for item in entity_matches or [])
    comparison_signal = _has_sales_comparison_intent(q_norm) and not _has_target_actual_comparison_intent(q_norm)

    def add(table_name: str, reason: str, confidence: float) -> None:
        if known_tables and table_name not in known_tables:
            return
        if not any(item["table"] == table_name for item in explicit):
            explicit.append({"table": table_name, "reason": reason, "confidence": confidence})

    # Rule 1: target queries always go only to m60_level
    if "target" in q_norm or "m60" in q_norm or _has_target_actual_comparison_intent(q_norm):
        add("m60_level", "m60_or_target_keyword", 0.88)
        return explicit

    # Rule 2: normal keyword routing
    if "industry" in q_norm or "market share" in q_norm or "market contribution" in q_norm or comparison_signal:
        add("industry_performance", "industry_or_market_keyword", 0.92)
    if "state" in q_norm or "district" in q_norm or "company" in q_norm or "companies" in q_norm:
        add("industry_performance", "geography_or_company_keyword", 0.95)
    if company_signal:
        add("industry_performance", "company_keyword", 0.90)
    if (
        ("mom" in q_norm or "actual" in q_norm or "day" in q_norm or "date" in q_norm)
        and not company_signal
    ):
        add("mom_level", "mom_actual_or_date_keyword", 0.84)

    if not explicit and not company_signal and (
        "volume" in q_norm or "sales" in q_norm or "product" in q_norm or "products" in q_norm
        or any(item.get("type") == "region" for item in entity_matches or [])
    ):
        add("mom_level", "volume_or_product_default", 0.80)

    # If both industry_performance and mom_level are matched, keep only the highest confidence one
    if len(explicit) > 1:
        explicit.sort(key=lambda x: x["confidence"], reverse=True)
        explicit = [explicit[0]]

    if explicit:
        return explicit

    # Fallback to schema context
    scored_context: Dict[str, float] = {}
    for item in schema_context or []:
        table = (item.get("table") or "").lower()
        if table:
            scored_context[table] = max(scored_context.get(table, 0.0), float(item.get("similarity", 0.0)))

    if scored_context:
        sorted_context = sorted(scored_context.items(), key=lambda pair: pair[1], reverse=True)
        return [
            {"table": sorted_context[0][0], "reason": "retrieved_schema_context", "confidence": round(sorted_context[0][1], 4)}
        ]

    return [{"table": table, "reason": "schema_default", "confidence": 0.35} for table in known_tables.keys()]


def _filter(
    table: Optional[str],
    column: str,
    operator: str,
    value: Any,
    source: str,
    is_business_rule: bool,
) -> Dict[str, Any]:
    return {
        "table": table,
        "column": column,
        "operator": operator,
        "value": value,
        "source": source,
        "is_business_rule": is_business_rule,
    }


def _columns_for(table: Optional[str], schema_index: Dict[str, Any]) -> set:
    if not table:
        return set()
    return set(schema_index.get("tables", {}).get(table, {}).get("columns", {}).keys())


def _first_existing(columns: set, candidates: List[str]) -> Optional[str]:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def _normalize(question: str) -> str:
    return re.sub(r"\s+", " ", (question or "").strip().lower())


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
        "columns", "data", "query",
    ]
    return not any(term in q_norm for term in business_terms)


def _infer_unit(q_norm: str) -> str:
    if re.search(r"\btmt\b", q_norm):
        return "TMT"
    if re.search(r"\bmt\b", q_norm):
        return "MT"
    if re.search(r"\bkl\b", q_norm):
        return "KL"
    return "TMT"


def _infer_aggregate_function(q_norm: str) -> Optional[str]:
    if any(term in q_norm for term in ["average", "avg", "mean"]):
        return "avg"
    if any(term in q_norm for term in ["maximum", "max"]):
        return "max"
    if any(term in q_norm for term in ["minimum", "min"]):
        return "min"
    if any(term in q_norm for term in ["total", "sum", "sales", "quantity", "actual", "target", "rank", "highest", "lowest"]):
        return "sum"
    return None


def _metric_alias(name: str, column: str, aggregation: str) -> str:
    unit_suffix = "tmt" if column.endswith("_tmt") else "kl" if column.endswith("_kl") else ""
    suffix = f"_{unit_suffix}" if unit_suffix else ""
    return f"{aggregation}_{name}{suffix}"


def _has_ranking_intent(q_norm: str) -> bool:
    return any(term in q_norm for term in ["rank", "ranking", "top", "highest", "lowest", "largest", "smallest", "best", "worst"])


def _contains_phrase(q_norm: str, phrase: str) -> bool:
    if len(phrase) <= 3:
        return bool(re.search(rf"\b{re.escape(phrase)}\b", q_norm))
    return phrase in q_norm


def _labeled_context(q_norm: str, value: str, label: str) -> bool:
    return bool(
        re.search(rf"\b{re.escape(value)}\b\s+{label}\b", q_norm)
        or re.search(rf"\b{label}\b\s+(?:is\s+)?\b{re.escape(value)}\b", q_norm)
    )


def _covered_by_longer_value(value: str, candidates: List[str]) -> bool:
    normalized = value.strip().casefold()
    return any(
        normalized != candidate.strip().casefold()
        and re.search(rf"\b{re.escape(normalized)}\b", candidate.strip().casefold())
        for candidate in candidates
    )


def _extract_state(q_norm: str) -> Optional[str]:
    for alias, state in sorted(STATE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", q_norm):
            return state
    return None


def _extract_named_entity_after(q_norm: str, label: str) -> Optional[str]:
    match = re.search(
        rf"\b{re.escape(label)}\s+([a-z0-9&.\- ]+?)(?=\s+(?:for|from|in|on|by|with|and|between|during|current|this|last|top|bottom)\b|$)",
        q_norm,
    )
    if not match:
        return None
    value = match.group(1).strip(" .,-")
    return value or None


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
        "north central": "North",
        "south central": "South",
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


def _map_zone_to_base_if_needed(zone_val: str, q_norm: str, table: str) -> str:
    if table in ("m60_level", "mom_level") and "hpcl mkt" not in q_norm:
        if zone_val in ("North West Frontier", "North Frontier Zone", "North Frontier"):
            return "North"
        if zone_val in ("South Western Zone", "South Western"):
            return "South"
    return zone_val


def _industry_zone_value(value: str) -> str:
    mapping = {
        "North": "NZ",
        "South": "SZ",
        "East": "EZ",
        "West": "WZ",
        "Central": "NCZ",
        "North Central": "NCZ",
        "South Central": "SCZ",
        "North West": "NWZ",
        "South Western Zone": "SZ",
        "North Frontier Zone": "NZ",
        "East Central Zone": "EZ",
        "Central Zone": "NCZ",
        "North Central Retail": "NCZ",
        "South Central Retail": "SCZ",
        "South Central LPG Zo": "SCZ",
        "North Central LPG Zo": "NCZ",
        "North West Retail Zo": "NWZ",
        "North West Frontier": "NWZ",
        "North West LPG Zone": "NWZ",
    }
    val_norm = str(value).strip().title()
    if val_norm.upper() in ["WZ", "SZ", "NWZ", "SCZ", "NZ", "EZ", "NCZ"]:
        return val_norm.upper()
    for prefix, abbr in mapping.items():
        if val_norm.startswith(prefix):
            return abbr
    return mapping.get(val_norm, value)


def _has_sales_comparison_intent(q_norm: str) -> bool:
    return any(term in q_norm for term in [
        "growth", "difference", "compare", "against", "year over year", "year-on-year", "yoy",
    ])


def _has_target_actual_comparison_intent(q_norm: str) -> bool:
    if "target" in q_norm:
        return True
    if ("growth" in q_norm or "difference" in q_norm) and len(_extract_fiscal_years(q_norm)) < 2:
        # Target comparisons are HPCL only; exclude competitor companies
        competitors = ["bpcl", "iocl", "cpcl", "nrl", "mrpl", "ril", "nel", "hmel", "shell", "pvt", "mpsu", "oth psu"]
        if any(c in q_norm for c in competitors):
            return False
        return True
    return False


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
    date_matches = list(re.finditer(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b",
        q_norm,
    ))
    if not date_matches:
        return None

    parsed_dates = []
    for match in date_matches[:2]:
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


def _dedupe_filters(filters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped = []
    for item in filters:
        key = (item.get("table"), item.get("column"), item.get("operator"), json.dumps(item.get("value"), sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _merge_duplicate_filters(filters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Group filters by (table, column)
    groups = {}
    for item in filters:
        if item.get("is_business_rule"):
            continue
        table = item.get("table")
        column = item.get("column")
        key = (table, column)
        if key not in groups:
            groups[key] = []
        groups[key].append(item)

    merged = []
    merged_keys = set()

    for key, items in groups.items():
        if len(items) > 1:
            mergeable = all(item.get("operator") in {"equal", "in"} for item in items)
            if mergeable:
                values = []
                for item in items:
                    val = item.get("value")
                    if isinstance(val, list):
                        values.extend(val)
                    else:
                        values.append(val)
                unique_values = []
                for v in values:
                    if v not in unique_values:
                        unique_values.append(v)
                
                first_item = items[0]
                merged_filter = {
                    "table": first_item.get("table"),
                    "column": first_item.get("column"),
                    "operator": "in" if len(unique_values) > 1 else "equal",
                    "value": unique_values if len(unique_values) > 1 else unique_values[0],
                    "source": first_item.get("source"),
                    "is_business_rule": False,
                }
                merged.append(merged_filter)
                merged_keys.add(key)

    final_filters = []
    for item in filters:
        if item.get("is_business_rule"):
            final_filters.append(item)
            continue
        key = (item.get("table"), item.get("column"))
        if key in merged_keys:
            continue
        final_filters.append(item)

    final_filters.extend(merged)
    return final_filters


def _dedupe_dimensions(dimensions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped = []
    for item in dimensions:
        key = (item.get("table"), item.get("column"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _score_confidence(plan: Dict[str, Any]) -> float:
    score = 0.2
    if plan["source_tables"]:
        score += min(plan["source_tables"][0].get("confidence", 0.0), 1.0) * 0.25
    if plan["metrics"]:
        score += 0.2
    if plan["filters"]:
        score += 0.15
    if plan["aggregations"]:
        score += 0.1
    if plan["group_by"]:
        score += 0.05
    if not plan["notes"]:
        score += 0.05
    return round(min(score, 0.95), 4)
