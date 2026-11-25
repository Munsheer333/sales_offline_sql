"""Resolve fiscal-year expressions into canonical YYYY-YYYY values.

Indian fiscal years run from April 1 through March 31.

Usage:
    python fiscal_year_resolver.py "Show sales for FY24-25"
    python fiscal_year_resolver.py "Show sales for Current FY"
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date


def resolve_fiscal_years(text: str, reference_date: date | None = None) -> dict[str, list[str]]:
    """Return canonical fiscal years found in text, preserving mention order."""
    reference_date = reference_date or date.today()
    normalized = re.sub(r"\s+", " ", str(text or "").strip().casefold())
    current = current_fiscal_year(reference_date)
    previous = previous_fiscal_year(current)
    found: list[tuple[int, str]] = []

    for match in re.finditer(r"\b(?:fy\s*)?(20\d{2})\s*[-/]\s*(20\d{2})\b", normalized):
        found.append((match.start(), _canonical_years(int(match.group(1)), int(match.group(2)))))

    for match in re.finditer(r"\bfy\s*([0-9]{2})\s*[-/]\s*([0-9]{2})\b", normalized):
        found.append((match.start(), _canonical_short_years(int(match.group(1)), int(match.group(2)))))

    relative_patterns = (
        (current, r"\b(?:current\s+fy|current\s+fiscal\s+year|current\s+financial\s+year|this\s+fy|cy)\b"),
        (previous, r"\b(?:last\s+fy|last\s+fiscal\s+year|last\s+financial\s+year|previous\s+fy|previous\s+fiscal\s+year|ly)\b"),
    )
    for canonical_year, pattern in relative_patterns:
        for match in re.finditer(pattern, normalized):
            found.append((match.start(), canonical_year))

    resolved = []
    for _position, canonical_year in sorted(found):
        if canonical_year not in resolved:
            resolved.append(canonical_year)
    return {"fiscal_years": resolved}


def current_fiscal_year(reference_date: date | None = None) -> str:
    reference_date = reference_date or date.today()
    start_year = reference_date.year if reference_date.month >= 4 else reference_date.year - 1
    return f"{start_year}-{start_year + 1}"


def previous_fiscal_year(canonical_year: str) -> str:
    start_year = int(canonical_year.split("-", 1)[0])
    return f"{start_year - 1}-{start_year}"


def _canonical_short_years(start_suffix: int, end_suffix: int) -> str:
    start_year = 2000 + start_suffix
    end_year = 2000 + end_suffix
    return _canonical_years(start_year, end_year)


def _canonical_years(start_year: int, end_year: int) -> str:
    if end_year != start_year + 1:
        raise ValueError(f"Invalid fiscal-year range: {start_year}-{end_year}")
    return f"{start_year:04d}-{end_year:04d}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve fiscal-year expressions.")
    parser.add_argument("text", help="Text containing fiscal-year expressions.")
    args = parser.parse_args()
    print(json.dumps(resolve_fiscal_years(args.text), indent=2))


if __name__ == "__main__":
    main()
