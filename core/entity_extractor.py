"""
NLPSQL SYSTEM — ENTITY EXTRACTOR MODULE
=======================================
Extracts structured business entities from a natural language question.

Supported entity types:
  - company       (via alias engine / metadata/aliases.json)
  - sbu           (via alias engine / metadata/aliases.json)
  - product       (via alias engine / metadata/aliases.json)
  - state         (via database discovery / metadata/entities.json)
  - district      (via database discovery / metadata/entities.json)
  - zone          (via alias engine / metadata/aliases.json)
  - region        (via database discovery / metadata/entities.json)
  - month         (APR, MAY, JUN, etc.)
  - year          (fiscal years matching YYYY-YYYY or YYYY)

Fuzzy matching is automatically attempted for all dimensions.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from difflib import SequenceMatcher
from core.alias_engine import get_alias_engine, AliasMatch
from core.metadata_discovery import load_entities

# ── Stopwords to avoid false fuzzy matches on small words ─────────────────────
_STOPWORDS = {
    "for", "in", "the", "and", "or", "of", "to", "by", "at", "on",
    "is", "are", "was", "a", "an", "show", "give", "get", "find",
    "with", "from", "as", "its", "it", "their", "this", "that", "be",
    "all", "per", "vs", "versus", "between", "across", "do", "does",
}

_MONTH_NAMES = ["APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC", "JAN", "FEB", "MAR"]


# ═════════════════════════════════════════════════════════════════════════════
# Structured Output Dataclass
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class ExtractionResult:
    companies: List[str] = field(default_factory=list)
    sbus: List[str] = field(default_factory=list)
    products: List[str] = field(default_factory=list)
    states: List[str] = field(default_factory=list)
    districts: List[str] = field(default_factory=list)
    zones: List[str] = field(default_factory=list)
    regions: List[str] = field(default_factory=list)
    months: List[str] = field(default_factory=list)
    years: List[str] = field(default_factory=list)  # Normalised to YYYY-YYYY e.g. "2025-2026"

    # Match metadata for debugging
    match_details: List[Dict[str, any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, List[str]]:
        return {
            "company": self.companies,
            "sbu": self.sbus,
            "product": self.products,
            "state": self.states,
            "district": self.districts,
            "zone": self.zones,
            "region": self.regions,
            "month": self.months,
            "year": self.years,
        }

    def __repr__(self) -> str:
        non_empty = {k: v for k, v in self.to_dict().items() if v}
        return f"ExtractionResult({non_empty})"


# ═════════════════════════════════════════════════════════════════════════════
# Entity Extractor
# ═════════════════════════════════════════════════════════════════════════════

class EntityExtractor:
    """
    Extracts and normalises all structured entities from a natural language question.
    """

    def __init__(self):
        self.alias_engine = get_alias_engine()
        # Load db-extracted entities
        catalog = load_entities(auto_refresh=True)
        self._states = {v.upper(): v for v in catalog.get("states", [])}
        self._districts = {v.upper(): v for v in catalog.get("districts", [])}
        self._regions = {v.upper(): v for v in catalog.get("regions", [])}
        self._fiscal_years = {v.upper(): v for v in catalog.get("fiscal_years", [])}

    def _normalise(self, text: str) -> str:
        return text.upper().strip()

    def _fuzzy_match(self, token: str, choices: Dict[str, str], threshold: float = 0.82) -> Optional[str]:
        """Match a token against dictionary keys using edit distance."""
        if len(token) < 3 or token.lower() in _STOPWORDS:
            return None
        best_ratio = 0.0
        best_match = None
        for key in choices:
            ratio = SequenceMatcher(None, token, key).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = choices[key]
        if best_ratio >= threshold:
            return best_match
        return None

    def extract(self, question: str) -> ExtractionResult:
        q_clean = question.strip()
        q_up = self._normalise(q_clean)

        res = ExtractionResult()

        # ── 1. Match via Alias Engine (companies, sbus, products, zones) ──────
        aliases = self.alias_engine.scan(q_clean)
        for a in aliases:
            detail = {
                "canonical": a.canonical,
                "category": a.category,
                "matched_text": a.matched_text,
                "method": a.method,
                "score": a.score,
            }
            res.match_details.append(detail)

            if a.category == "companies" and a.canonical not in res.companies:
                res.companies.append(a.canonical)
            elif a.category == "sbus" and a.canonical not in res.sbus:
                res.sbus.append(a.canonical)
            elif a.category == "products" and a.canonical not in res.products:
                res.products.append(a.canonical)
            elif a.category == "zones" and a.canonical not in res.zones:
                res.zones.append(a.canonical)

        # ── 2. Parse Years & Fiscal Years (e.g. "2024-2025", "FY25-26", "24-25") ──
        # Match YYYY-YYYY, YYYY-YY, YY-YY, or YY-YYYY
        fy_pattern = r"\b(?:FY)?\s*(\d{2,4})[-–](\d{2,4})\b"
        fy_matches = re.findall(fy_pattern, q_up)
        for start, end in fy_matches:
            if len(start) == 2:
                start = f"20{start}"
            if len(end) == 2:
                end = f"20{end}"
            val = f"{start}-{end}"
            if val not in res.years:
                res.years.append(val)
                res.match_details.append({"canonical": val, "category": "years", "matched_text": f"{start[-2:]}-{end[-2:]}", "method": "regex", "score": 1.0})

        # Match single YYYY as a fiscal year prefix (e.g. "FY 2024" -> "2024-2025")
        single_yr_pattern = r"\b(?:FY)?\s*(20\d{2})\b"
        single_matches = re.findall(single_yr_pattern, q_up)
        for yr in single_matches:
            # Skip if already captured in a range
            in_range = False
            for existing in res.years:
                if yr in existing:
                    in_range = True
                    break
            if not in_range:
                # Convert 2024 to 2024-2025
                val = f"{yr}-{int(yr)+1}"
                if val not in res.years:
                    res.years.append(val)
                    res.match_details.append({"canonical": val, "category": "years", "matched_text": yr, "method": "regex_single", "score": 0.9})

        # ── 3. Parse Month Names (APR, MAY, JUN, etc.) ────────────────────────
        for m in _MONTH_NAMES:
            if re.search(rf"\b{m}\b", q_up):
                if m not in res.months:
                    res.months.append(m)
                    res.match_details.append({"canonical": m, "category": "months", "matched_text": m, "method": "exact", "score": 1.0})

        # ── 4. Scan Question for States, Districts, Regions ───────────────────
        tokens = re.split(r"[\s,/]+", q_up)
        bigrams = [f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens) - 1)]
        trigrams = [f"{tokens[i]} {tokens[i+1]} {tokens[i+2]}" for i in range(len(tokens) - 2)]
        candidates = tokens + bigrams + trigrams

        # Exact scan first for states, districts, regions
        for phrase in sorted(candidates, key=len, reverse=True):
            # Check state exact
            if phrase in self._states:
                val = self._states[phrase]
                if val not in res.states:
                    res.states.append(val)
                    res.match_details.append({"canonical": val, "category": "states", "matched_text": phrase, "method": "exact", "score": 1.0})
            # Check district exact
            if phrase in self._districts:
                val = self._districts[phrase]
                if val not in res.districts:
                    res.districts.append(val)
                    res.match_details.append({"canonical": val, "category": "districts", "matched_text": phrase, "method": "exact", "score": 1.0})
            # Check region exact
            if phrase in self._regions:
                val = self._regions[phrase]
                if val not in res.regions:
                    res.regions.append(val)
                    res.match_details.append({"canonical": val, "category": "regions", "matched_text": phrase, "method": "exact", "score": 1.0})

        # Fuzzy fallback for words not matched exactly
        for token in tokens:
            if token in _STOPWORDS:
                continue
            # If token wasn't already part of an exact state/district/region match
            matched_any = any(token in self._normalise(d["matched_text"]) for d in res.match_details)
            if not matched_any:
                # Try state fuzzy
                matched_state = self._fuzzy_match(token, self._states)
                if matched_state and matched_state not in res.states:
                    res.states.append(matched_state)
                    res.match_details.append({"canonical": matched_state, "category": "states", "matched_text": token, "method": "fuzzy", "score": 0.85})
                    continue
                # Try district fuzzy
                matched_district = self._fuzzy_match(token, self._districts)
                if matched_district and matched_district not in res.districts:
                    res.districts.append(matched_district)
                    res.match_details.append({"canonical": matched_district, "category": "districts", "matched_text": token, "method": "fuzzy", "score": 0.85})
                    continue
                # Try region fuzzy
                matched_region = self._fuzzy_match(token, self._regions)
                if matched_region and matched_region not in res.regions:
                    res.regions.append(matched_region)
                    res.match_details.append({"canonical": matched_region, "category": "regions", "matched_text": token, "method": "fuzzy", "score": 0.85})

        return res


# ── Module-level singleton ────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_entity_extractor() -> EntityExtractor:
    return EntityExtractor()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    extractor = EntityExtractor()

    test_queries = [
        "Show total HPCL sales in Maharashtra district Pune for FY 2024-25 in APR",
        "Compare petrol and diesel for BPCL in ECZ zone",
        "What are the LPG sales in Assm for Mumbai region in FY 2024?",
    ]

    for q in test_queries:
        print(f"\nQ: {q}")
        res = extractor.extract(q)
        print("Structured Output:")
        for k, v in res.to_dict().items():
            if v:
                print(f"  {k:10s}: {v}")
        print("Matches:")
        for d in res.match_details:
            print(f"  - {d['category']}: '{d['matched_text']}' -> {d['canonical']} ({d['method']} score={d['score']})")
