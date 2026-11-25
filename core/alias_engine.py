"""
NLPSQL SYSTEM — ALIAS ENGINE
==============================
Resolves user-provided entity mentions to canonical database values.

Features:
  - Exact match       (case-insensitive)
  - Alias match       (e.g. "petrol" → "MS", "diesel" → "HSD")
  - Fuzzy match       (edit-distance based, configurable threshold)
  - Multi-word scan   (finds aliases embedded in a sentence)
  - Dynamic loading   (reads metadata/aliases.json — no hardcoding)

Usage:
    from core.alias_engine import AliasEngine
    engine = AliasEngine()

    # Resolve a single token
    result = engine.resolve("diesel")
    # → AliasMatch(canonical="HSD", category="products", method="alias", score=1.0)

    # Resolve with fuzzy fallback
    result = engine.resolve("diesl")       # typo
    # → AliasMatch(canonical="HSD", category="products", method="fuzzy", score=0.89)

    # Scan a whole question
    matches = engine.scan("Show HPCL and BPCL petrol sales")
    # → [("HPCL", "companies"), ("BPCL", "companies"), ("MS", "products")]
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Optional fuzzy matching via difflib (stdlib) ──────────────────────────────
from difflib import SequenceMatcher

_ALIASES_PATH = Path(__file__).resolve().parent.parent / "metadata" / "aliases.json"

# Minimum similarity ratio for fuzzy matches (0.0 – 1.0)
FUZZY_THRESHOLD = 0.78
# Minimum token length to attempt fuzzy matching (avoids false positives on 2-char words)
FUZZY_MIN_TOKEN_LEN = 3

# Words that should never trigger a fuzzy entity match
_STOPWORDS = {
    "for", "in", "the", "and", "or", "of", "to", "by", "at", "on",
    "is", "are", "was", "a", "an", "show", "give", "get", "find",
    "with", "from", "as", "its", "it", "their", "this", "that", "be",
    "all", "per", "vs", "versus", "between", "across", "do", "does",
}


# ═════════════════════════════════════════════════════════════════════════════
# Data classes
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class AliasMatch:
    canonical: str         # the DB-ready canonical value  e.g. "HPCL"
    category: str          # "companies" | "sbus" | "products" | "zones"
    column: str            # the DB column  e.g. "comname"
    tables: List[str]      # tables where this column exists
    method: str            # "exact" | "alias" | "fuzzy"
    score: float           # 1.0 for exact/alias, 0.0-1.0 for fuzzy
    matched_text: str = "" # the input text that triggered this match

    def __repr__(self) -> str:
        return (
            f"AliasMatch({self.canonical!r} [{self.category}] "
            f"via {self.method}({self.score:.2f}) on {self.matched_text!r})"
        )


# ═════════════════════════════════════════════════════════════════════════════
# Internal index builder
# ═════════════════════════════════════════════════════════════════════════════

def _normalise(text: str) -> str:
    """Lowercase, strip diacritics, collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_str.lower().strip())


def _fuzzy_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class _IndexEntry:
    canonical: str
    category: str
    column: str
    tables: List[str]


def _build_index(raw: dict) -> Tuple[Dict[str, _IndexEntry], List[Tuple[str, _IndexEntry]]]:
    """
    Build two lookup structures from aliases.json:
      - exact_map: normalised_string → _IndexEntry  (O(1) lookup)
      - fuzzy_list: [(normalised_string, _IndexEntry)]  (for fuzzy scan)
    """
    exact_map: Dict[str, _IndexEntry] = {}
    fuzzy_list: List[Tuple[str, _IndexEntry]] = []

    for category, category_data in raw.items():
        if category.startswith("_"):
            continue
        for canonical, meta in category_data.items():
            # Determine column and tables
            column = meta.get("column", "")
            tables = meta.get("tables") or ([meta["table"]] if "table" in meta else [])

            entry = _IndexEntry(
                canonical=meta.get("canonical", canonical),
                category=category,
                column=column,
                tables=tables,
            )

            # Index canonical itself
            norm_canonical = _normalise(canonical)
            exact_map[norm_canonical] = entry
            fuzzy_list.append((norm_canonical, entry))

            # Index each alias
            for alias in meta.get("aliases", []):
                norm_alias = _normalise(alias)
                if norm_alias not in exact_map:
                    exact_map[norm_alias] = entry
                fuzzy_list.append((norm_alias, entry))

    return exact_map, fuzzy_list


# ═════════════════════════════════════════════════════════════════════════════
# Alias Engine
# ═════════════════════════════════════════════════════════════════════════════

class AliasEngine:
    """
    Resolves entity strings to canonical DB values using exact, alias,
    and fuzzy matching. All definitions come from metadata/aliases.json.
    """

    def __init__(
        self,
        path: Path = _ALIASES_PATH,
        fuzzy_threshold: float = FUZZY_THRESHOLD,
    ):
        self._threshold = fuzzy_threshold
        raw = json.loads(path.read_text(encoding="utf-8"))
        self._exact_map, self._fuzzy_list = _build_index(raw)

    # ── Public API ────────────────────────────────────────────────────────────

    def resolve(
        self,
        text: str,
        category: Optional[str] = None,
        fuzzy: bool = True,
    ) -> Optional[AliasMatch]:
        """
        Resolve a single word/phrase to a canonical value.

        Args:
            text:     Input string (e.g. "petrol", "Hindustan Petroleum", "diesl")
            category: Optional filter — only match within this category
                      ("companies", "sbus", "products", "zones")
            fuzzy:    Whether to attempt fuzzy matching as a fallback

        Returns:
            AliasMatch or None
        """
        norm = _normalise(text)

        # 1. Exact / alias match
        entry = self._exact_map.get(norm)
        if entry and (category is None or entry.category == category):
            method = "exact" if norm == _normalise(entry.canonical) else "alias"
            return AliasMatch(
                canonical=entry.canonical,
                category=entry.category,
                column=entry.column,
                tables=entry.tables,
                method=method,
                score=1.0,
                matched_text=text,
            )

        # 2. Fuzzy match
        if fuzzy and len(norm) >= FUZZY_MIN_TOKEN_LEN:
            best_score = 0.0
            best_entry: Optional[_IndexEntry] = None
            best_key = ""
            for key, e in self._fuzzy_list:
                if category and e.category != category:
                    continue
                score = _fuzzy_ratio(norm, key)
                if score > best_score:
                    best_score = score
                    best_entry = e
                    best_key = key
            if best_entry and best_score >= self._threshold:
                return AliasMatch(
                    canonical=best_entry.canonical,
                    category=best_entry.category,
                    column=best_entry.column,
                    tables=best_entry.tables,
                    method="fuzzy",
                    score=round(best_score, 4),
                    matched_text=text,
                )

        return None

    def resolve_batch(
        self,
        texts: List[str],
        category: Optional[str] = None,
        fuzzy: bool = True,
    ) -> Dict[str, Optional[AliasMatch]]:
        """Resolve a list of strings. Returns {input_text: AliasMatch or None}."""
        return {t: self.resolve(t, category=category, fuzzy=fuzzy) for t in texts}

    def scan(
        self,
        question: str,
        fuzzy: bool = True,
    ) -> List[AliasMatch]:
        """
        Scan a full natural language question for entity mentions.
        Tries all n-grams (1 to 4 words) to catch multi-word aliases.
        Returns a deduplicated list of AliasMatch objects (longest match wins).
        """
        norm_q = _normalise(question)
        tokens = norm_q.split()
        n = len(tokens)

        matched_spans: Dict[str, AliasMatch] = {}  # canonical → best match
        used_positions: set = set()

        # Try ngrams from longest to shortest to prioritise precise matches
        for size in range(min(4, n), 0, -1):
            for i in range(n - size + 1):
                span_positions = set(range(i, i + size))
                if span_positions & used_positions:
                    continue  # already consumed by a longer match
                phrase = " ".join(tokens[i: i + size])
                # Skip stopwords for single-token fuzzy to prevent false positives
                if size == 1 and phrase in _STOPWORDS:
                    continue
                result = self.resolve(phrase, fuzzy=(fuzzy and size == 1))
                if result:
                    key = f"{result.category}:{result.canonical}"
                    # Prefer alias/exact over fuzzy; prefer longer phrase
                    existing = matched_spans.get(key)
                    if not existing or result.score > existing.score or (
                        result.method != "fuzzy" and existing.method == "fuzzy"
                    ):
                        result.matched_text = phrase
                        matched_spans[key] = result
                        used_positions |= span_positions

        return list(matched_spans.values())

    def canonical(self, text: str, category: Optional[str] = None) -> Optional[str]:
        """Shorthand — return just the canonical string or None."""
        match = self.resolve(text, category=category)
        return match.canonical if match else None

    def all_canonicals(self, category: str) -> List[str]:
        """Return all canonical values for a category."""
        seen = set()
        result = []
        for entry in self._exact_map.values():
            if entry.category == category and entry.canonical not in seen:
                seen.add(entry.canonical)
                result.append(entry.canonical)
        return sorted(result)


# ── Module-level singleton ────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_alias_engine() -> AliasEngine:
    """Return a cached singleton AliasEngine instance."""
    return AliasEngine()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    engine = AliasEngine()

    print("=== Exact / alias resolution ===")
    tests = [
        "HPCL", "Hindustan Petroleum", "iocl", "bharat petroleum",
        "diesel", "petrol", "motor spirit", "NG", "i&c", "natural gas",
        "I and C", "MS", "HSD", "kerosene", "furnace oil",
        "ecz", "north zone", "NCZ",
    ]
    for t in tests:
        r = engine.resolve(t)
        if r:
            print(f"  {t!r:30s} → {r.canonical:20s} [{r.category}] via {r.method}({r.score:.2f})")
        else:
            print(f"  {t!r:30s} → ❌ no match")

    print("\n=== Fuzzy matching (typos) ===")
    fuzzy_tests = [
        "Hindostan Petroleum",  # typo
        "diesl",                # typo
        "hpcle",                # typo
        "petrolleum",           # typo
        "IOCCL",                # extra char
        "relience",             # misspelling
    ]
    for t in fuzzy_tests:
        r = engine.resolve(t, fuzzy=True)
        if r:
            print(f"  {t!r:25s} → {r.canonical:15s} [{r.category}] fuzzy={r.score:.2f}")
        else:
            print(f"  {t!r:25s} → ❌ no match")

    print("\n=== Sentence scan ===")
    questions = [
        "Show HPCL and BPCL petrol sales in Maharashtra",
        "Compare diesel and MS sales for IOCL",
        "Monthly LPG sales for Bharat Petroleum in North Zone",
        "Natural gas sales in ECZ zone for FY 2024-2025",
    ]
    for q in questions:
        matches = engine.scan(q)
        print(f"\n  Q: {q}")
        for m in matches:
            print(f"     → {m.canonical:20s} [{m.category}] via {m.method} on '{m.matched_text}'")

    print("\n=== All canonicals ===")
    for cat in ["companies", "sbus", "products", "zones"]:
        print(f"  {cat}: {engine.all_canonicals(cat)}")
