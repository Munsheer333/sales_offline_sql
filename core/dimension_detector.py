"""
NLPSQL SYSTEM — DIMENSION DETECTOR
===================================
Identifies dimension grouping signals in natural language queries and maps
them to corresponding database columns for GROUP BY planning.

Patterns supported:
  - "each company", "company wise", "by company", "per company"   -> company (comname)
  - "each zone", "zone wise", "by zone", "per zone"               -> zone (zone_name)
  - "each region", "region wise", "by region", "per region"       -> region (ro / region_name)
  - "each district", "district wise", "by district", "per district" -> district (distname)
  - "each state", "state wise", "by state", "per state"           -> state (statename)
  - "each product", "product wise", "by product", "per product"   -> product (productname)
  - "each sbu", "sbu wise", "by sbu", "per sbu"                   -> sbu (sbu_name)
  - "monthly", "month wise", "by month", "per month"              -> month (month_name / yearmonth)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Dict, List, Any, Optional

from core.semantic_registry import get_registry


@dataclass
class DetectedDimension:
    concept: str
    column: str
    matched_phrase: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Map regex phrases to semantic registry concepts
_DIMENSION_SIGNALS: Dict[str, List[str]] = {
    "company": [
        r"\beach\s*compan(y|ies)\b",
        r"\bcompan(y|ies)\s*wise\b",
        r"\bby\s*compan(y|ies)\b",
        r"\bper\s*compan(y|ies)\b",
        r"\bcompan(y|ies)\-wise\b",
    ],
    "zone": [
        r"\beach\s*zone\b",
        r"\bzone\s*wise\b",
        r"\bby\s*zone\b",
        r"\bper\s*zone\b",
        r"\bzone\-wise\b",
    ],
    "region": [
        r"\beach\s*region\b",
        r"\bregion\s*wise\b",
        r"\bby\s*region\b",
        r"\bper\s*region\b",
        r"\bregion\-wise\b",
        r"\bby\s*ro\b",
        r"\bro\s*wise\b",
    ],
    "district": [
        r"\beach\s*district\b",
        r"\bdistrict\s*wise\b",
        r"\bby\s*district\b",
        r"\bper\s*district\b",
        r"\bdistrict\-wise\b",
    ],
    "state": [
        r"\beach\s*state\b",
        r"\bstate\s*wise\b",
        r"\bby\s*state\b",
        r"\bper\s*state\b",
        r"\bstate\-wise\b",
    ],
    "product": [
        r"\beach\s*product\b",
        r"\bproduct\s*wise\b",
        r"\bby\s*product\b",
        r"\bper\s*product\b",
        r"\bproduct\-wise\b",
    ],
    "sbu": [
        r"\beach\s*sbu\b",
        r"\bsbu\s*wise\b",
        r"\bby\s*sbu\b",
        r"\bper\s*sbu\b",
        r"\bsbu\-wise\b",
    ],
    "month": [
        r"\bmonthly\b",
        r"\bmonth\s*wise\b",
        r"\bby\s*month\b",
        r"\bper\s*month\b",
        r"\bmonth\-wise\b",
    ],
}


class DimensionDetector:
    """
    Scans questions for grouping dimensions programmatically.
    """

    def __init__(self):
        self.registry = get_registry()
        # Precompile regex signals
        self._compiled: Dict[str, List[re.Pattern]] = {}
        for concept, patterns in _DIMENSION_SIGNALS.items():
            self._compiled[concept] = [re.compile(p, re.IGNORECASE) for p in patterns]

    def detect(self, question: str, table_name: str = "industry_performance") -> List[DetectedDimension]:
        """
        Scan query for dimension grouping signals.

        Args:
            question: Natural language question.
            table_name: Context table to resolve column mappings.

        Returns:
            List of detected dimensions with mapped DB columns.
        """
        q = question.strip()
        detected: List[DetectedDimension] = []
        seen_concepts = set()

        for concept, patterns in self._compiled.items():
            for pat in patterns:
                match = pat.search(q)
                if match and concept not in seen_concepts:
                    # Resolve column name from semantic registry
                    col = self.registry.resolve_group_by_column(concept, table_name)
                    if col:
                        detected.append(DetectedDimension(
                            concept=concept,
                            column=col,
                            matched_phrase=match.group(0)
                        ))
                        seen_concepts.add(concept)
                        break

        # Also support implicit groupings based on questions like:
        # "Which product had the highest MOM growth?"
        # or "Top 5 districts by sales" (plural dimension name)
        plural_maps = {
            "company": r"\b(companies|firms|omcs)\b",
            "zone": r"\bzones\b",
            "region": r"\bregions\b",
            "district": r"\bdistricts\b",
            "state": r"\bstates\b",
            "product": r"\bproducts\b",
            "sbu": r"\bsbus\b",
        }
        for concept, pattern in plural_maps.items():
            if concept not in seen_concepts and re.search(pattern, q, re.IGNORECASE):
                col = self.registry.resolve_group_by_column(concept, table_name)
                if col:
                    detected.append(DetectedDimension(
                        concept=concept,
                        column=col,
                        matched_phrase=re.search(pattern, q, re.IGNORECASE).group(0)
                    ))
                    seen_concepts.add(concept)

        if "product" not in seen_concepts and re.search(r"\bwhich\s*product\b", q, re.IGNORECASE):
            col = self.registry.resolve_group_by_column("product", table_name)
            if col:
                detected.append(DetectedDimension(
                    concept="product",
                    column=col,
                    matched_phrase="which product"
                ))
                seen_concepts.add("product")

        if "company" not in seen_concepts and re.search(r"\bwhich\s*company\b", q, re.IGNORECASE):
            col = self.registry.resolve_group_by_column("company", table_name)
            if col:
                detected.append(DetectedDimension(
                    concept="company",
                    column=col,
                    matched_phrase="which company"
                ))
                seen_concepts.add("company")

        return detected


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_dimension_detector() -> DimensionDetector:
    return DimensionDetector()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    detector = DimensionDetector()

    test_queries = [
        "Show sales monthly and company wise",
        "Compare MS sales by state",
        "What is the target per sbu?",
        "Top 5 districts by sales",
        "Which product had the highest MOM growth?",
        "Show zone wise sales for HPCL in North Zone",
    ]

    print("=== Dimension Detection Tests ===")
    for q in test_queries:
        print(f"\nQ: {q}")
        dims = detector.detect(q)
        for d in dims:
            print(f"  -> Concept: {d.concept:10s} | Column: {d.column:15s} | Phrase: '{d.matched_phrase}'")
