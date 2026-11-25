"""Standalone business alias dictionary generator and fuzzy resolver.

Usage:
    python alias_resolver.py --generate
    python alias_resolver.py --resolve "hindustan petroleam"
"""

from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


BUSINESS_ALIASES = {
    "companies": {
        "HPCL": ["Hindustan Petroleum", "Hindustan Petroleum Corporation Limited"],
        "IOCL": ["Indian Oil", "Indian Oil Corporation Limited"],
        "BPCL": ["Bharat Petroleum", "Bharat Petroleum Corporation Limited"],
    },
    "products": {
        "MS": ["Motor Spirit"],
        "HSD": ["High Speed Diesel"],
    },
    "sbus": {
        "NG": ["Natural Gas"],
        "I&C": ["Industrial & Commercial", "Industrial and Commercial"],
    },
}


def generate_alias_dictionary(metadata_path: str = "business_metadata.json") -> dict[str, Any]:
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(entity_type: str, canonical_name: str, alias: str, source: str) -> None:
        key = (entity_type, _normalize(canonical_name), _normalize(alias))
        if not key[1] or not key[2] or key in seen:
            return
        seen.add(key)
        entries.append({
            "entity_type": entity_type,
            "canonical_name": canonical_name,
            "alias": alias,
            "normalized_alias": key[2],
            "source": source,
        })

    for entity_type, values in metadata.items():
        for item in values:
            canonical_name = item["canonical_name"]
            add(entity_type, canonical_name, canonical_name, "database_metadata")
            for alias in item.get("aliases", []):
                add(entity_type, canonical_name, alias, "database_metadata")

    for entity_type, values in BUSINESS_ALIASES.items():
        for canonical_name, aliases in values.items():
            add(entity_type, canonical_name, canonical_name, "business_alias")
            for alias in aliases:
                add(entity_type, canonical_name, alias, "business_alias")

    entries.sort(key=lambda item: (item["entity_type"], item["canonical_name"].casefold(), item["alias"].casefold()))
    return {
        "version": 1,
        "matching": {
            "normalization": "casefold, punctuation folding, whitespace collapse",
            "default_fuzzy_threshold": 0.78,
        },
        "entries": entries,
    }


def resolve_alias(
    query: str,
    dictionary: dict[str, Any],
    entity_type: str | None = None,
    fuzzy_threshold: float | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    normalized_query = _normalize(query)
    threshold = fuzzy_threshold if fuzzy_threshold is not None else dictionary["matching"]["default_fuzzy_threshold"]
    candidates = [
        item for item in dictionary["entries"]
        if entity_type is None or item["entity_type"] == entity_type
    ]
    matches = []
    for item in candidates:
        score = 1.0 if normalized_query == item["normalized_alias"] else SequenceMatcher(
            None, normalized_query, item["normalized_alias"]
        ).ratio()
        if score >= threshold:
            matches.append({
                "entity_type": item["entity_type"],
                "canonical_name": item["canonical_name"],
                "matched_alias": item["alias"],
                "match_type": "exact" if score == 1.0 else "fuzzy",
                "score": round(score, 4),
            })

    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    for item in matches:
        key = (item["entity_type"], item["canonical_name"])
        if key not in deduplicated or item["score"] > deduplicated[key]["score"]:
            deduplicated[key] = item
    return sorted(deduplicated.values(), key=lambda item: (-item["score"], item["canonical_name"]))[:limit]


def _normalize(value: str) -> str:
    normalized = str(value or "").casefold().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and query the business alias dictionary.")
    parser.add_argument("--generate", action="store_true", help="Generate alias_dictionary.json.")
    parser.add_argument("--metadata", default="business_metadata.json", help="Input business metadata JSON.")
    parser.add_argument("--output", default="alias_dictionary.json", help="Alias dictionary JSON file.")
    parser.add_argument("--resolve", help="Resolve an exact or fuzzy business alias.")
    parser.add_argument("--entity-type", help="Optional entity type filter.")
    parser.add_argument("--threshold", type=float, help="Optional fuzzy matching threshold.")
    args = parser.parse_args()

    output = Path(args.output)
    if args.generate:
        dictionary = generate_alias_dictionary(args.metadata)
        output.write_text(json.dumps(dictionary, indent=2) + "\n", encoding="utf-8")
        print(f"Generated {len(dictionary['entries'])} aliases in {output}")

    if args.resolve:
        dictionary = json.loads(output.read_text(encoding="utf-8"))
        print(json.dumps(resolve_alias(args.resolve, dictionary, args.entity_type, args.threshold), indent=2))

    if not args.generate and not args.resolve:
        parser.error("use --generate, --resolve, or both")


if __name__ == "__main__":
    main()
