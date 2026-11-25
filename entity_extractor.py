"""Standalone canonical business entity extraction.

Usage:
    python entity_extractor.py "Show HPCL HSD sales in South zone during FY 2024-2025"
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ENTITY_TYPES = {
    "companies": "company",
    "sbus": "sbu",
    "products": "product",
    "regions": "region",
    "zones": "zone",
    "districts": "district",
}
MONTHS = {
    "jan": "JAN", "january": "JAN",
    "feb": "FEB", "february": "FEB",
    "mar": "MAR", "march": "MAR",
    "apr": "APR", "april": "APR",
    "may": "MAY",
    "jun": "JUN", "june": "JUN",
    "jul": "JUL", "july": "JUL",
    "aug": "AUG", "august": "AUG",
    "sep": "SEP", "sept": "SEP", "september": "SEP",
    "oct": "OCT", "october": "OCT",
    "nov": "NOV", "november": "NOV",
    "dec": "DEC", "december": "DEC",
}


def extract_entities(
    question: str,
    alias_path: str = "alias_dictionary.json",
    fuzzy_threshold: float = 0.84,
) -> dict[str, dict[str, list[str]]]:
    """Extract canonical entities from a natural-language analytics question."""
    dictionary = json.loads(Path(alias_path).read_text(encoding="utf-8"))
    normalized_question = _normalize(question)
    entities: dict[str, list[str]] = defaultdict(list)
    occupied_spans: dict[str, list[tuple[int, int]]] = defaultdict(list)

    entries = sorted(dictionary["entries"], key=lambda item: len(item["normalized_alias"]), reverse=True)
    for item in entries:
        output_type = ENTITY_TYPES.get(item["entity_type"])
        alias = item["normalized_alias"]
        if not output_type or not alias:
            continue
        for match in _find_phrases(normalized_question, alias):
            span = match.span()
            if any(_overlaps(span, existing) for existing in occupied_spans[output_type]):
                continue
            if _has_conflicting_label(normalized_question, span, output_type):
                continue
            occupied_spans[output_type].append(span)
            _add(entities, output_type, item["canonical_name"])

    for output_type, phrase in _labeled_phrases(normalized_question):
        if entities.get(output_type):
            continue
        fuzzy_match = _best_fuzzy_match(phrase, output_type, entries, fuzzy_threshold)
        if fuzzy_match:
            _add(entities, output_type, fuzzy_match)

    for match in re.finditer(r"\b(?:fy\s*)?(20\d{2})\s*[-/]\s*(20\d{2})\b", question.casefold()):
        _add(entities, "year", f"{match.group(1)}-{match.group(2)}")

    for month, canonical_name in MONTHS.items():
        if re.search(rf"\b{re.escape(month)}\b", normalized_question):
            _add(entities, "month", canonical_name)

    return {"entities": dict(entities)}


def _labeled_phrases(normalized_question: str) -> list[tuple[str, str]]:
    labels = {
        "company": "company",
        "sbu": "sbu",
        "product": "product",
        "region": "region",
        "zone": "zone",
        "district": "district",
    }
    stop_words = r"(?:for|in|from|during|between|and|with|fy|fiscal|year|month|company|sbu|product|region|zone|district)"
    phrases = []
    for label, output_type in labels.items():
        for pattern in [
            rf"\b{label}\s+([a-z0-9& ]+?)(?=\s+{stop_words}\b|$)",
            rf"\b([a-z0-9& ]+?)\s+{label}\b",
        ]:
            match = re.search(pattern, normalized_question)
            if match:
                phrase = match.group(1).strip()
                if phrase:
                    phrases.append((output_type, phrase))
    return phrases


def _best_fuzzy_match(
    phrase: str,
    output_type: str,
    entries: list[dict[str, Any]],
    fuzzy_threshold: float,
) -> str | None:
    source_type = next((source for source, target in ENTITY_TYPES.items() if target == output_type), "")
    best: tuple[float, str] | None = None
    for item in entries:
        if item["entity_type"] != source_type:
            continue
        score = SequenceMatcher(None, phrase, item["normalized_alias"]).ratio()
        if score >= fuzzy_threshold and (best is None or score > best[0]):
            best = (score, item["canonical_name"])
    return best[1] if best else None


def _find_phrases(value: str, phrase: str):
    return re.finditer(rf"(?<!\S){re.escape(phrase)}(?!\S)", value)


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _has_conflicting_label(value: str, span: tuple[int, int], output_type: str) -> bool:
    following = value[span[1]:].lstrip()
    match = re.match(r"(company|sbu|product|region|zone|district)\b", following)
    return bool(match and match.group(1) != output_type)


def _normalize(value: str) -> str:
    normalized = str(value or "").casefold().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _add(entities: dict[str, list[str]], entity_type: str, value: str) -> None:
    if value not in entities[entity_type]:
        entities[entity_type].append(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract canonical business entities.")
    parser.add_argument("question", help="Natural-language analytics question.")
    parser.add_argument("--aliases", default="alias_dictionary.json", help="Alias dictionary JSON file.")
    parser.add_argument("--fuzzy-threshold", type=float, default=0.84, help="Minimum fuzzy label match.")
    args = parser.parse_args()
    print(json.dumps(extract_entities(args.question, args.aliases, args.fuzzy_threshold), indent=2))


if __name__ == "__main__":
    main()
