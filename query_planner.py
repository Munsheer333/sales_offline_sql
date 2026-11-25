"""Standalone natural-language analytics query planner.

This module creates an inspectable semantic plan only. It does not generate SQL.

Usage:
    python query_planner.py "Show top 10 zones by market share for LY"
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from entity_extractor import extract_entities
from fiscal_year_resolver import resolve_fiscal_years
from intent_detector import detect_intent


def build_query_plan(question: str) -> dict[str, Any]:
    intent_result = detect_intent(question)
    entities = extract_entities(question)["entities"]
    entities = _resolve_entity_collisions(question, entities)
    fiscal_years = resolve_fiscal_years(question)["fiscal_years"]
    if fiscal_years:
        entities["year"] = fiscal_years

    return {
        "intent": intent_result["intent"],
        "entities": entities,
        "filters": _build_filters(entities),
        "dimensions": _infer_dimensions(question, intent_result["intent"]),
        "metrics": _infer_metrics(intent_result["intent"]),
    }


def _resolve_entity_collisions(question: str, entities: dict[str, list[str]]) -> dict[str, list[str]]:
    normalized = re.sub(r"\s+", " ", str(question or "").strip().casefold())
    sbus = entities.get("sbu", [])
    products = entities.get("product", [])
    collisions = {value.casefold() for value in sbus} & {value.casefold() for value in products}
    if not collisions:
        return entities

    if not re.search(r"\bproduct(?:s)?\b", normalized):
        entities["product"] = [value for value in products if value.casefold() not in collisions]
        if not entities["product"]:
            entities.pop("product")
    elif not re.search(r"\bsbu(?:s|'s)?\b|\bstrategic business unit", normalized):
        entities["sbu"] = [value for value in sbus if value.casefold() not in collisions]
        if not entities["sbu"]:
            entities.pop("sbu")
    return entities


def _build_filters(entities: dict[str, list[str]]) -> list[dict[str, Any]]:
    filters = []
    for entity_type in ["company", "sbu", "product", "region", "zone", "district", "year", "month"]:
        values = entities.get(entity_type, [])
        if values:
            filters.append({
                "field": entity_type,
                "operator": "in" if len(values) > 1 else "equal",
                "value": values if len(values) > 1 else values[0],
            })
    return filters


def _infer_dimensions(question: str, intent: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(question or "").strip().casefold())
    dimensions = []
    patterns = {
        "company": r"\b(?:company|companies)\b",
        "sbu": r"\b(?:sbu|sbus|sbu's|strategic business units?)\b",
        "product": r"\b(?:product|products)\b",
        "region": r"\b(?:region|regions|regional)\b",
        "zone": r"\b(?:zone|zones|zonal)\b",
        "district": r"\b(?:district|districts)\b",
        "month": r"\b(?:month|months|monthly|month over month)\b",
        "year": r"\b(?:year|years|fy|fiscal year|year over year|yoy)\b",
    }
    for dimension, pattern in patterns.items():
        if re.search(pattern, normalized):
            dimensions.append(dimension)

    default_dimension = {
        "region_analysis": "region",
        "zone_analysis": "zone",
        "district_analysis": "district",
        "trend": "month",
    }.get(intent)
    if default_dimension and default_dimension not in dimensions:
        dimensions.append(default_dimension)
    return dimensions


def _infer_metrics(intent: str) -> list[str]:
    return {
        "total_sales": ["total_sales_tmt"],
        "market_share": ["total_sales_tmt", "market_share_percentage"],
        "growth": ["previous_sales_tmt", "current_sales_tmt", "growth_percentage"],
        "sales_difference": ["previous_sales_tmt", "current_sales_tmt", "sales_difference_tmt"],
        "comparison": ["total_sales_tmt"],
        "top_n": ["total_sales_tmt"],
        "bottom_n": ["total_sales_tmt"],
        "trend": ["total_sales_tmt"],
        "actual_volume": ["actual_volume_tmt"],
        "region_analysis": ["total_sales_tmt"],
        "zone_analysis": ["total_sales_tmt"],
        "district_analysis": ["total_sales_tmt"],
    }[intent]


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an analytics query plan without SQL generation.")
    parser.add_argument("question", help="Natural-language analytics question.")
    args = parser.parse_args()
    print(json.dumps(build_query_plan(args.question), indent=2))


if __name__ == "__main__":
    main()
