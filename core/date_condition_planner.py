"""
NLPSQL SYSTEM — DATE CONDITION PLANNER
=======================================
Parses natural language date references and plans structured query conditions
using '=', 'IN', and 'BETWEEN' operators on date/time columns.

Columns targeted:
  - yearmonth   (integer YYYYMM format)
  - month_name  (string 'APR' - 'MAR' format)
  - fiscal_year (string 'YYYY-YYYY' format)

Supported patterns:
  - "December 2025"        -> yearmonth = 202512
  - "Last month"           -> yearmonth = YYYYMM (dynamically computed)
  - "Last quarter"         -> yearmonth BETWEEN YYYYMM and YYYYMM
  - "1 Dec to 15 Dec 2025" -> yearmonth = 202512 (month-level resolution)
  - "Jan to Mar 2025"      -> yearmonth BETWEEN 202501 AND 202503
"""

from __future__ import annotations

import re
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple


_MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_MONTH_NAMES = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


@dataclass
class DateCondition:
    column: str              # "yearmonth" | "month_name" | "fiscal_year"
    operator: str            # "=" | "IN" | "BETWEEN"
    values: List[Any]        # List of values to filter by (integers or strings)
    description: str         # Human-friendly explanation of match

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DateConditionPlanner:
    """
    Parses and constructs structured date condition plans.
    Calculates dynamic relative dates relative to a system or anchor date.
    """

    def __init__(self, anchor_date: Optional[datetime] = None):
        self._anchor = anchor_date or datetime.now()

    def _get_yearmonth(self, year: int, month: int) -> int:
        return year * 100 + month

    def _get_fiscal_year(self, year: int, month: int) -> str:
        if month < 4:
            return f"{year-1}-{year}"
        return f"{year}-{year+1}"

    def plan(self, text: str) -> Optional[List[DateCondition]]:
        """
        Main parser method. Returns a list of planned date conditions or None.
        """
        norm = text.lower().strip()
        conditions: List[DateCondition] = []

        # ── 1. "Last month" ───────────────────────────────────────────────────
        if re.search(r"\blast\s*month\b", norm):
            # Compute last month YYYYMM relative to anchor
            yr = self._anchor.year
            mon = self._anchor.month
            if mon == 1:
                last_yr = yr - 1
                last_mon = 12
            else:
                last_yr = yr
                last_mon = mon - 1
            ym_val = self._get_yearmonth(last_yr, last_mon)
            conditions.append(DateCondition(
                column="yearmonth",
                operator="=",
                values=[ym_val],
                description=f"Last month relative to anchor {self._anchor.strftime('%Y-%m')}"
            ))
            return conditions

        # ── 2. "Last quarter" ─────────────────────────────────────────────────
        if re.search(r"\blast\s*quarter\b", norm):
            # Quarters: Q1 (Apr-Jun), Q2 (Jul-Sep), Q3 (Oct-Dec), Q4 (Jan-Mar)
            # Find current quarter, then go back 1 quarter
            yr = self._anchor.year
            mon = self._anchor.month
            
            # Determine current quarter: 1=Jan-Mar, 2=Apr-Jun, 3=Jul-Sep, 4=Oct-Dec
            if mon in [1, 2, 3]:
                # Last quarter was Oct-Dec of previous year
                start_ym = self._get_yearmonth(yr - 1, 10)
                end_ym = self._get_yearmonth(yr - 1, 12)
            elif mon in [4, 5, 6]:
                # Last quarter was Jan-Mar of current year
                start_ym = self._get_yearmonth(yr, 1)
                end_ym = self._get_yearmonth(yr, 3)
            elif mon in [7, 8, 9]:
                # Last quarter was Apr-Jun of current year
                start_ym = self._get_yearmonth(yr, 4)
                end_ym = self._get_yearmonth(yr, 6)
            else:
                # Last quarter was Jul-Sep of current year
                start_ym = self._get_yearmonth(yr, 7)
                end_ym = self._get_yearmonth(yr, 9)

            conditions.append(DateCondition(
                column="yearmonth",
                operator="BETWEEN",
                values=[start_ym, end_ym],
                description=f"Last quarter relative to anchor {self._anchor.strftime('%Y-%m')}"
            ))
            return conditions

        # ── 3. Range with Month Names: e.g. "Jan to Mar 2025" or "Jan to Mar" ─
        m_range = re.search(
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+to\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?:\s+(20\d{2}))?\b",
            norm
        )
        if m_range:
            start_mon_name = m_range.group(1)
            end_mon_name = m_range.group(2)
            year_str = m_range.group(3)
            
            start_num = _MONTH_MAP[start_mon_name]
            end_num = _MONTH_MAP[end_mon_name]
            
            # Default year to current or previous depending on values
            year_val = int(year_str) if year_str else self._anchor.year
            
            if year_str:
                # We have a specific year, generate BETWEEN on yearmonth
                start_ym = self._get_yearmonth(year_val, start_num)
                end_ym = self._get_yearmonth(year_val, end_num)
                # Handle cross-year wrap if start_num > end_num (e.g. Dec to Feb)
                if start_num > end_num:
                    end_ym = self._get_yearmonth(year_val + 1, end_num)
                conditions.append(DateCondition(
                    column="yearmonth",
                    operator="BETWEEN",
                    values=[start_ym, end_ym],
                    description=f"Range from {start_mon_name.upper()} to {end_mon_name.upper()} {year_val}"
                ))
            else:
                # No year, generate IN condition for month names
                # Collect all months in the range
                month_list = []
                curr = start_num
                while True:
                    month_list.append(_MONTH_NAMES[curr - 1])
                    if curr == end_num:
                        break
                    curr = (curr % 12) + 1
                conditions.append(DateCondition(
                    column="month_name",
                    operator="IN",
                    values=month_list,
                    description=f"Months in range {start_mon_name.upper()} to {end_mon_name.upper()}"
                ))
            return conditions

        # ── 4. Daily Range within Month: e.g. "1 Dec to 15 Dec 2025" or "1 Dec to 15 Dec" ──
        m_daily = re.search(
            r"\b\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+to\s+\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?:\s+(20\d{2}))?\b",
            norm
        )
        if m_daily:
            start_mon_name = m_daily.group(1)
            end_mon_name = m_daily.group(2)
            year_str = m_daily.group(3)
            
            start_num = _MONTH_MAP[start_mon_name]
            end_num = _MONTH_MAP[end_mon_name]
            year_val = int(year_str) if year_str else self._anchor.year
            
            if start_num == end_num:
                # Same month, e.g. 1 Dec to 15 Dec 2025 -> yearmonth = 202512
                ym_val = self._get_yearmonth(year_val, start_num)
                conditions.append(DateCondition(
                    column="yearmonth",
                    operator="=",
                    values=[ym_val],
                    description=f"Daily range falling in month {start_mon_name.upper()} {year_val}"
                ))
            else:
                # Spans months, e.g. 15 Nov to 15 Dec 2025 -> yearmonth BETWEEN 202511 AND 202512
                start_ym = self._get_yearmonth(year_val, start_num)
                end_ym = self._get_yearmonth(year_val, end_num)
                if start_num > end_num:
                    end_ym = self._get_yearmonth(year_val + 1, end_num)
                conditions.append(DateCondition(
                    column="yearmonth",
                    operator="BETWEEN",
                    values=[start_ym, end_ym],
                    description=f"Daily range spanning months {start_mon_name.upper()} to {end_mon_name.upper()} {year_val}"
                ))
            return conditions

        # ── 5. Specific Month and Year: e.g. "December 2025" or "Dec 2025" ───
        m_month_year = re.search(
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(20\d{2})\b",
            norm
        )
        if m_month_year:
            mon_name = m_month_year.group(1)
            year_str = m_month_year.group(2)
            
            mon_num = _MONTH_MAP[mon_name]
            year_val = int(year_str)
            ym_val = self._get_yearmonth(year_val, mon_num)
            
            conditions.append(DateCondition(
                column="yearmonth",
                operator="=",
                values=[ym_val],
                description=f"Specific month {mon_name.upper()} {year_val}"
            ))
            return conditions

        return None


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_date_condition_planner() -> DateConditionPlanner:
    return DateConditionPlanner()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Test with system time set to June 15, 2026
    anchor = datetime(2026, 6, 15)
    planner = DateConditionPlanner(anchor_date=anchor)

    print("=== Date Condition Planner Smoke Tests ===")
    print(f"Anchor Date: {anchor.strftime('%Y-%m-%d')}\n")

    test_cases = [
        "December 2025",
        "Last month",
        "Last quarter",
        "1 Dec to 15 Dec 2025",
        "15 Nov to 15 Dec 2025",
        "Jan to Mar 2025",
        "Jan to Mar",
    ]

    for tc in test_cases:
        plan = planner.plan(tc)
        print(f"Input: '{tc}'")
        if plan:
            for cond in plan:
                print(f"  -> Column: {cond.column}")
                print(f"     Operator: {cond.operator}")
                print(f"     Values: {cond.values}")
                print(f"     Desc: {cond.description}")
        else:
            print("  -> ❌ No match")
        print()
