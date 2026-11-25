"""
NLPSQL SYSTEM — SEMANTIC REGISTRY LOADER
=========================================
Provides fast access to the semantic registry for column resolution,
alias matching, and business rule retrieval.

Usage:
    from core.semantic_registry import SemanticRegistry
    reg = SemanticRegistry()

    # Resolve a concept to a DB column for a given table
    col = reg.resolve_column("sales", "industry_performance")   # → "netweight_tmt"
    col = reg.resolve_column("company", "industry_performance") # → "comname"

    # Match a free-text word to a concept
    concept = reg.match_concept("offtake")   # → "sales"
    concept = reg.match_concept("state")     # → "state"

    # Get mandatory WHERE clauses for a table
    rules = reg.mandatory_filters("industry_performance")
    # → ["(category != 'O' OR category IS NULL)"]

    # Get the aggregation expression for a metric
    expr = reg.metric_expression("sales", "industry_performance")
    # → "ROUND(SUM(netweight_tmt)::numeric, 2)"
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "metadata" / "semantic_registry.json"


class SemanticRegistry:
    """In-memory view of metadata/semantic_registry.json."""

    def __init__(self, path: Path = _REGISTRY_PATH):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self._concepts: Dict[str, dict] = raw.get("concepts", {})
        self._rules: Dict[str, list] = raw.get("business_rules", {})
        self._priority: List[str] = raw.get("concept_priority", [])
        # Build alias index: normalised_alias → concept_key
        self._alias_index: Dict[str, str] = {}
        for concept, meta in self._concepts.items():
            self._alias_index[concept.lower()] = concept
            for alias in meta.get("aliases", []):
                self._alias_index[alias.lower()] = concept

    # ── Public API ────────────────────────────────────────────────────────────

    def resolve_column(self, concept: str, table: str) -> Optional[str]:
        """Return the primary database column for a concept on a specific table."""
        meta = self._concepts.get(concept, {})
        mapping = meta.get("mappings", {}).get(table, {})
        return mapping.get("column")

    def resolve_group_by_column(self, concept: str, table: str) -> Optional[str]:
        """Return the GROUP BY column for a concept on a specific table."""
        meta = self._concepts.get(concept, {})
        mapping = meta.get("mappings", {}).get(table, {})
        return mapping.get("group_by_column") or mapping.get("column")

    def metric_expression(self, concept: str, table: str, unit: str = "TMT") -> Optional[str]:
        """Return the SQL aggregation expression for a metric concept."""
        meta = self._concepts.get(concept, {})
        mapping = meta.get("mappings", {}).get(table, {})
        if unit == "MT":
            return mapping.get("unit_mt_expression") or mapping.get("expression")
        return mapping.get("expression")

    def metric_alias(self, concept: str, table: str) -> Optional[str]:
        """Return the output alias for a metric column."""
        meta = self._concepts.get(concept, {})
        mapping = meta.get("mappings", {}).get(table, {})
        return mapping.get("alias")

    def match_concept(self, word: str) -> Optional[str]:
        """
        Match a single word or phrase to a registered concept.
        Returns the concept key (e.g. 'sales', 'company') or None.
        """
        normalised = word.lower().strip()
        return self._alias_index.get(normalised)

    def match_concepts_in_question(self, question: str) -> Dict[str, str]:
        """
        Scan a question for all registered concept aliases.
        Returns dict of { concept_key: matched_alias }.
        Longer matches take priority (avoids 'sales' matching inside 'market share').
        """
        q_lower = question.lower()
        found: Dict[str, str] = {}
        # Sort by alias length descending so longer phrases match first
        for alias in sorted(self._alias_index, key=len, reverse=True):
            if re.search(rf"\b{re.escape(alias)}\b", q_lower):
                concept = self._alias_index[alias]
                if concept not in found:
                    found[concept] = alias
        return found

    def mandatory_filters(self, table: str) -> List[str]:
        """Return list of mandatory SQL WHERE snippets for a table."""
        return [
            rule["sql"]
            for rule in self._rules.get(table, [])
            if rule.get("mandatory", False)
        ]

    def concept_type(self, concept: str, table: str) -> Optional[str]:
        """Return the type of a concept mapping (dimension, filter, ratio, computed, mandatory_filter)."""
        meta = self._concepts.get(concept, {})
        return meta.get("mappings", {}).get(table, {}).get("type")

    def all_concepts(self) -> List[str]:
        """Return all registered concept keys in priority order."""
        ordered = [c for c in self._priority if c in self._concepts]
        remainder = [c for c in self._concepts if c not in self._priority]
        return ordered + remainder

    def concept_tables(self, concept: str) -> List[str]:
        """Return all tables where this concept has a mapping."""
        return list(self._concepts.get(concept, {}).get("mappings", {}).keys())


# ── Module-level singleton ────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_registry() -> SemanticRegistry:
    """Return a cached singleton instance of SemanticRegistry."""
    return SemanticRegistry()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    reg = get_registry()

    print("=== Column resolution ===")
    tests = [
        ("sales",       "industry_performance"),
        ("company",     "industry_performance"),
        ("sbu",         "mom_level"),
        ("product",     "industry_performance"),
        ("state",       "industry_performance"),
        ("district",    "industry_performance"),
        ("zone",        "industry_performance"),
        ("region",      "industry_performance"),
        ("month",       "industry_performance"),
        ("fiscal_year", "industry_performance"),
        ("region",      "mom_level"),
    ]
    for concept, table in tests:
        col = reg.resolve_column(concept, table)
        print(f"  {concept:15s} → [{table}].{col}")

    print("\n=== Alias matching ===")
    aliases = ["offtake", "omc", "tmt", "financial year", "monthly", "fuel", "fy", "province"]
    for alias in aliases:
        print(f"  '{alias}' → {reg.match_concept(alias)}")

    print("\n=== Concept scan in question ===")
    q = "Show monthly HPCL market share by state for FY 2024-2025"
    matched = reg.match_concepts_in_question(q)
    print(f"  Q: {q}")
    for c, a in matched.items():
        print(f"    {c} (via '{a}')")

    print("\n=== Mandatory filters ===")
    for table in ["industry_performance", "mom_level"]:
        filters = reg.mandatory_filters(table)
        print(f"  [{table}]: {filters}")

    print("\n=== Metric expressions ===")
    print(f"  sales TMT: {reg.metric_expression('sales', 'industry_performance', 'TMT')}")
    print(f"  sales MT:  {reg.metric_expression('sales', 'industry_performance', 'MT')}")
