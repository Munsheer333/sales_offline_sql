"""Standalone deterministic intent detection for offline sales analytics.

Usage:
    python intent_detector.py "What is total sales for HPCL?"
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import Callable


SUPPORTED_INTENTS = (
    "total_sales",
    "market_share",
    "growth",
    "sales_difference",
    "comparison",
    "top_n",
    "bottom_n",
    "trend",
    "actual_volume",
    "region_analysis",
    "zone_analysis",
    "district_analysis",
)


@dataclass(frozen=True)
class IntentRule:
    intent: str
    confidence: float
    matcher: Callable[[str], bool]


def detect_intent(question: str) -> dict[str, str | float]:
    """Return the dominant supported intent and deterministic confidence."""
    normalized = _normalize(question)
    for rule in _rules():
        if rule.matcher(normalized):
            return {"intent": rule.intent, "confidence": rule.confidence}
    return {"intent": "total_sales", "confidence": 0.45}


def _rules() -> tuple[IntentRule, ...]:
    return (
        IntentRule("market_share", 0.98, lambda q: _contains_any(q, "market share", "market contribution")),
        IntentRule("sales_difference", 0.97, lambda q: _contains_any(q, "sales difference", "difference in sales", "sales variance")),
        IntentRule("growth", 0.96, lambda q: _contains_any(q, "growth", "year over year", "year-on-year", "yoy", "grew", "increase percentage")),
        IntentRule("trend", 0.94, lambda q: _contains_any(q, "trend", "month over month", "mom trend", "time series", "over time")),
        IntentRule("top_n", 0.94, lambda q: bool(re.search(r"\b(?:top|highest|largest|best)\b", q))),
        IntentRule("bottom_n", 0.94, lambda q: bool(re.search(r"\b(?:bottom|lowest|least|smallest|worst)\b", q))),
        IntentRule("comparison", 0.90, lambda q: _contains_any(q, "compare", "comparison", " versus ", " vs ", " against ")),
        IntentRule("actual_volume", 0.90, lambda q: _contains_any(q, "actual volume", "actual sales", "netweight", "volume sold")),
        IntentRule("district_analysis", 0.88, lambda q: bool(re.search(r"\b(?:district|districts)\b", q))),
        IntentRule("region_analysis", 0.88, lambda q: bool(re.search(r"\b(?:region|regions|regional)\b", q))),
        IntentRule("zone_analysis", 0.88, lambda q: bool(re.search(r"\b(?:zone|zones|zonal)\b", q))),
        IntentRule("total_sales", 0.86, lambda q: _contains_any(q, "total sales", "sales", "business generated", "business was generated", "revenue", "sold")),
    )


def _contains_any(value: str, *terms: str) -> bool:
    return any(term in value for term in terms)


def _normalize(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip().casefold())
    return f" {normalized} "


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect the dominant sales analytics intent.")
    parser.add_argument("question", help="Natural-language analytics question.")
    args = parser.parse_args()
    print(json.dumps(detect_intent(args.question), indent=2))


if __name__ == "__main__":
    main()
