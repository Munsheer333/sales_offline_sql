"""
NLPSQL SYSTEM — FISCAL YEAR RESOLVER
====================================
Dynamically resolves relative and absolute temporal mentions into canonical
fiscal year strings (format: YYYY-YYYY, e.g., '2025-2026').

Supports:
  - "current FY" / "this fiscal year" -> e.g. "2026-2027" (or overridden by config)
  - "last FY" / "previous fiscal year" -> e.g. "2025-2026"
  - "CY" / "current year" -> e.g. "2026-2027" (canonical format)
  - "LY" / "last year" -> e.g. "2025-2026"
  - "FY24-25" / "FY 2024-2025" / "FY 25-26" / "24-25" -> normalized to "YYYY-YYYY"

All calculations are based on the current date (Indian FY begins April 1).
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from typing import Optional

import config


class FiscalYearResolver:
    """
    Resolves year mentions dynamically based on the current system date
    or an optional anchor datetime for testing.
    """

    def __init__(self, anchor_date: Optional[datetime] = None):
        self._anchor = anchor_date or datetime.now()

    def get_current_fy(self) -> str:
        """
        Calculate current financial year based on the anchor date.
        If config.CURRENT_FINANCIAL_YEAR is set, we respect that as the default.
        """
        # If config is set, use it at runtime
        if getattr(config, "CURRENT_FINANCIAL_YEAR", None):
            return config.CURRENT_FINANCIAL_YEAR

        # Dynamic calculation: Indian FY starts April 1
        year = self._anchor.year
        if self._anchor.month < 4:
            return f"{year-1}-{year}"
        else:
            return f"{year}-{year+1}"

    def get_previous_fy(self) -> str:
        """Calculate the financial year preceding the current one."""
        current = self.get_current_fy()
        parts = current.split("-")
        if len(parts) == 2:
            try:
                start = int(parts[0]) - 1
                end = int(parts[1]) - 1
                return f"{start}-{end}"
            except ValueError:
                pass
        # Fallback based on anchor date
        year = self._anchor.year
        if self._anchor.month < 4:
            return f"{year-2}-{year-1}"
        else:
            return f"{year-1}-{year}"

    def resolve(self, text: str) -> Optional[str]:
        """
        Parse and resolve a relative or absolute year string.

        Examples:
          "current fy" -> "2025-2026" (or current)
          "last fy" -> "2024-2025"
          "cy" -> "2025-2026" (current year)
          "ly" -> "2024-2025" (last year)
          "FY24-25" -> "2024-2025"
          "25-26" -> "2025-2026"

        Returns:
          Canonical FY string (YYYY-YYYY) or None if no match.
        """
        norm = text.lower().strip()

        # ── 1. Relative Mentions ──────────────────────────────────────────────
        current_mentions = {
            "current fy", "this fy", "current fiscal year", "this fiscal year",
            "current financial year", "this financial year", "cy", "current year"
        }
        if norm in current_mentions:
            return self.get_current_fy()

        last_mentions = {
            "last fy", "previous fy", "last fiscal year", "previous fiscal year",
            "last financial year", "previous financial year", "ly", "last year"
        }
        if norm in last_mentions:
            return self.get_previous_fy()

        # ── 2. Absolute Mentions ──────────────────────────────────────────────
        # Match YYYY-YYYY or YYYY-YY (e.g. 2024-2025, 2024-25, FY 24-25, FY 2024-2025)
        m_range = re.search(r"\b(?:fy)?\s*(\d{2,4})[-–](\d{2,4})\b", norm)
        if m_range:
            start, end = m_range.group(1), m_range.group(2)
            # Expand start year
            if len(start) == 2:
                start = f"20{start}"
            # Expand end year
            if len(end) == 2:
                end = f"20{end}"
            return f"{start}-{end}"

        # Match single year with FY/CY prefix (e.g. "fy 2024", "fy24")
        m_single = re.search(r"\b(?:fy|cy)?\s*(20\d{2}|\d{2})\b", norm)
        if m_single:
            yr = m_single.group(1)
            if len(yr) == 2:
                yr = f"20{yr}"
            # Convert single year to standard financial year prefix (e.g. 2024 -> 2024-2025)
            try:
                start_yr = int(yr)
                return f"{start_yr}-{start_yr+1}"
            except ValueError:
                pass

        return None


# ── Module-level singleton ────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_fiscal_year_resolver() -> FiscalYearResolver:
    return FiscalYearResolver()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Test with current date
    resolver = FiscalYearResolver()
    print("=== Dynamic Year Resolution (System Time) ===")
    print(f"  Current FY:  {resolver.get_current_fy()}")
    print(f"  Previous FY: {resolver.get_previous_fy()}")

    tests = [
        "current FY", "last FY", "CY", "LY",
        "FY24-25", "FY25-26", "23-24", "FY 2024", "fy25"
    ]
    print("\n=== Resolving Mentions ===")
    for t in tests:
        print(f"  {t:15s} → {resolver.resolve(t)}")

    # Test with custom anchor date (e.g., February 2028 - before April)
    print("\n=== Anchor Test (Feb 15, 2028) ===")
    anchor = datetime(2028, 2, 15)
    # Temporarily unset env default for calculation test
    import os
    orig_env = os.environ.get("CURRENT_FINANCIAL_YEAR")
    if "CURRENT_FINANCIAL_YEAR" in os.environ:
        del os.environ["CURRENT_FINANCIAL_YEAR"]
    # Re-import config to clear default, or construct manual resolver
    custom_resolver = FiscalYearResolver(anchor_date=anchor)
    # Monkey-patch config parameter for anchor testing
    import config
    orig_config = config.CURRENT_FINANCIAL_YEAR
    config.CURRENT_FINANCIAL_YEAR = None
    
    print(f"  Current FY (Feb 2028):  {custom_resolver.get_current_fy()}")
    print(f"  Previous FY (Feb 2028): {custom_resolver.get_previous_fy()}")
    
    # Restore original config
    config.CURRENT_FINANCIAL_YEAR = orig_config
