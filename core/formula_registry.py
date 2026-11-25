"""
NLPSQL SYSTEM — FORMULA ENGINE / REGISTRY LOADER
==================================================
Loads and renders business formula SQL templates from metadata/formula_registry.json.

Prevents prompt pollution and hardcoding by isolating SQL logic from code.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, Any, Optional

_FORMULA_PATH = Path(__file__).resolve().parent.parent / "metadata" / "formula_registry.json"


class FormulaRegistry:
    """
    Renders complex SQL formulas from the formula store dynamically.
    """

    def __init__(self, path: Path = _FORMULA_PATH):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self._formulas: Dict[str, dict] = raw.get("formulas", {})

    def render_actual_volume(
        self,
        column: str = "netweight_tmt",
        unit: str = "TMT"
    ) -> Dict[str, str]:
        """
        Renders raw volume aggregation.
        """
        f = self._formulas.get("actual_volume", {})
        temp = f.get("templates", {}).get(unit, {})
        expr = temp.get("expression", "SUM({column})").format(column=column)
        alias = temp.get("alias", f"actual_sales_{unit.lower()}")
        return {"expression": expr, "alias": alias}

    def render_market_share(
        self,
        company: Optional[str] = None,
        comname_col: str = "comname",
        sales_col: str = "netweight_tmt"
    ) -> Dict[str, str]:
        """
        Renders market share SQL.
        If company is provided, renders company-specific market share.
        Otherwise, renders dynamic overall market share (using OVER Window).
        """
        f = self._formulas.get("market_share", {})
        if company:
            temp = f.get("templates", {}).get("company_specific", {})
            expr = temp.get("expression", "").format(
                comname_col=comname_col,
                company=company,
                sales_col=sales_col
            )
            # Normalise alias name
            comp_clean = company.replace(" ", "_").replace("&", "and").lower()
            alias = temp.get("alias", "market_share_pct").format(
                company=company,
                company_lower=comp_clean
            )
        else:
            temp = f.get("templates", {}).get("all_companies", {})
            expr = temp.get("expression", "").format(sales_col=sales_col)
            alias = temp.get("alias", "market_share_pct")

        return {"expression": expr, "alias": alias}

    def render_growth(
        self,
        sales_col: str = "netweight_tmt",
        partition_col: str = "comname",
        order_col: str = "yearmonth"
    ) -> Dict[str, str]:
        """
        Renders YoY/MoM growth percentage using window lag.
        """
        f = self._formulas.get("growth_pct", {})
        temp = f.get("templates", {}).get("window_lag", {})
        expr = temp.get("expression", "").format(
            sales_col=sales_col,
            partition_col=partition_col,
            order_col=order_col
        )
        alias = temp.get("alias", "growth_pct")
        return {"expression": expr, "alias": alias}

    def render_sales_difference(
        self,
        company_a: Optional[str] = None,
        company_b: Optional[str] = None,
        period_a: Optional[str] = None,
        period_b: Optional[str] = None,
        comname_col: str = "comname",
        period_col: str = "fiscal_year",
        sales_col: str = "netweight_tmt"
    ) -> Dict[str, str]:
        """
        Renders difference delta calculations between two companies or periods.
        """
        f = self._formulas.get("sales_difference", {})
        if company_a and company_b:
            temp = f.get("templates", {}).get("company_delta", {})
            expr = temp.get("expression", "").format(
                comname_col=comname_col,
                company_a=company_a,
                company_b=company_b,
                sales_col=sales_col
            )
            alias = temp.get("alias", "").format(
                company_a=company_a.lower(),
                company_b=company_b.lower()
            )
        elif period_a and period_b:
            temp = f.get("templates", {}).get("period_delta", {})
            expr = temp.get("expression", "").format(
                period_col=period_col,
                period_a=period_a,
                period_b=period_b,
                sales_col=sales_col
            )
            alias = temp.get("alias", "").format(
                period_a=period_a.replace("-", "_"),
                period_b=period_b.replace("-", "_")
            )
        else:
            expr = "0"
            alias = "diff"

        return {"expression": expr, "alias": alias}


# ── Module-level singleton ────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_formula_registry() -> FormulaRegistry:
    return FormulaRegistry()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    registry = FormulaRegistry()

    print("=== Formula Render Tests ===")
    
    # 1. Actual Volume
    v_tmt = registry.render_actual_volume(unit="TMT")
    print(f"\nActual Volume (TMT):\n  SQL:   {v_tmt['expression']}\n  Alias: {v_tmt['alias']}")
    
    v_mt = registry.render_actual_volume(unit="MT")
    print(f"Actual Volume (MT):\n  SQL:   {v_mt['expression']}\n  Alias: {v_mt['alias']}")

    # 2. Market Share
    ms_comp = registry.render_market_share(company="HPCL")
    print(f"\nMarket Share (HPCL):\n  SQL:   {ms_comp['expression']}\n  Alias: {ms_comp['alias']}")

    ms_all = registry.render_market_share()
    print(f"Market Share (All):\n  SQL:   {ms_all['expression']}\n  Alias: {ms_all['alias']}")

    # 3. Growth
    growth = registry.render_growth(sales_col="netweight_tmt", partition_col="comname", order_col="yearmonth")
    print(f"\nGrowth %:\n  SQL:   {growth['expression']}\n  Alias: {growth['alias']}")

    # 4. Sales Difference
    diff_comp = registry.render_sales_difference(company_a="HPCL", company_b="BPCL")
    print(f"\nSales Difference (HPCL vs BPCL):\n  SQL:   {diff_comp['expression']}\n  Alias: {diff_comp['alias']}")

    diff_year = registry.render_sales_difference(period_a="2024-2025", period_b="2025-2026")
    print(f"Sales Difference (Years):\n  SQL:   {diff_year['expression']}\n  Alias: {diff_year['alias']}")
