"""
NLPSQL SYSTEM — CONDITIONAL FILTER PLANNER
===========================================
Generates a structured filter plan based on extracted entities and semantic
mappings. It uses the "Lazy Filtering" paradigm — adding filters ONLY if
the corresponding entity was detected in the prompt.

Output format:
{
  "filters": [
    {
      "concept": "company",
      "column": "comname",
      "operator": "=",      # or "IN" for multiple values
      "values": ["HPCL"],
      "type": "dimension"
    },
    ...
  ]
}

No SQL is generated in this stage.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from core.semantic_registry import get_registry
from core.entity_extractor import ExtractionResult, get_entity_extractor
from core.fiscal_year_resolver import get_fiscal_year_resolver


@dataclass
class FilterPlanItem:
    concept: str
    column: str
    operator: str
    values: List[str]
    type: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ConditionalFilterPlanner:
    """
    Plans filters dynamically using the semantic registry and extraction results.
    Never generates hardcoded queries; outputs structured filters only.
    """

    def __init__(self):
        self.registry = get_registry()
        self.fy_resolver = get_fiscal_year_resolver()

    def plan(self, entities: ExtractionResult, table_name: str = "industry_performance") -> Dict[str, List[Dict[str, Any]]]:
        """
        Builds a list of filter plans for the given table.

        Args:
            entities: Extracted entities from the EntityExtractor.
            table_name: Database table context to resolve columns.

        Returns:
            Dict containing a list of planned filters.
        """
        filters: List[FilterPlanItem] = []

        # 1. Company Filter
        if entities.companies:
            col = self.registry.resolve_column("company", table_name)
            if col:
                filters.append(FilterPlanItem(
                    concept="company",
                    column=col,
                    operator="IN" if len(entities.companies) > 1 else "=",
                    values=entities.companies,
                    type="dimension"
                ))

        # 2. SBU Filter
        if entities.sbus:
            col = self.registry.resolve_column("sbu", table_name)
            if col:
                filters.append(FilterPlanItem(
                    concept="sbu",
                    column=col,
                    operator="IN" if len(entities.sbus) > 1 else "=",
                    values=entities.sbus,
                    type="dimension"
                ))

        # 3. Product Filter
        if entities.products:
            col = self.registry.resolve_column("product", table_name)
            if col:
                filters.append(FilterPlanItem(
                    concept="product",
                    column=col,
                    operator="IN" if len(entities.products) > 1 else "=",
                    values=entities.products,
                    type="dimension"
                ))

        # 4. State Filter
        if entities.states:
            col = self.registry.resolve_column("state", table_name)
            if col:
                filters.append(FilterPlanItem(
                    concept="state",
                    column=col,
                    operator="IN" if len(entities.states) > 1 else "=",
                    values=entities.states,
                    type="dimension"
                ))

        # 5. District Filter
        if entities.districts:
            col = self.registry.resolve_column("district", table_name)
            if col:
                filters.append(FilterPlanItem(
                    concept="district",
                    column=col,
                    operator="IN" if len(entities.districts) > 1 else "=",
                    values=entities.districts,
                    type="dimension"
                ))

        # 6. Zone Filter
        if entities.zones:
            col = self.registry.resolve_column("zone", table_name)
            if col:
                filters.append(FilterPlanItem(
                    concept="zone",
                    column=col,
                    operator="IN" if len(entities.zones) > 1 else "=",
                    values=entities.zones,
                    type="dimension"
                ))

        # 7. Region Filter
        if entities.regions:
            col = self.registry.resolve_column("region", table_name)
            if col:
                filters.append(FilterPlanItem(
                    concept="region",
                    column=col,
                    operator="IN" if len(entities.regions) > 1 else "=",
                    values=entities.regions,
                    type="dimension"
                ))

        # 8. Fiscal Year Filter
        if entities.years:
            col = self.registry.resolve_column("fiscal_year", table_name)
            if col:
                resolved_years = []
                for yr in entities.years:
                    val = self.fy_resolver.resolve(yr)
                    if val:
                        resolved_years.append(val)
                if resolved_years:
                    filters.append(FilterPlanItem(
                        concept="fiscal_year",
                        column=col,
                        operator="IN" if len(resolved_years) > 1 else "=",
                        values=resolved_years,
                        type="filter"
                    ))

        # 9. Month Filter
        if entities.months:
            col = self.registry.resolve_column("month", table_name)
            if col:
                filters.append(FilterPlanItem(
                    concept="month",
                    column=col,
                    operator="IN" if len(entities.months) > 1 else "=",
                    values=entities.months,
                    type="dimension"
                ))

        return {
            "filters": [f.to_dict() for f in filters]
        }


# ── Module-level singleton ────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_filter_planner() -> ConditionalFilterPlanner:
    return ConditionalFilterPlanner()


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    extractor = get_entity_extractor()
    planner = ConditionalFilterPlanner()

    test_queries = [
        "HPCL sales in Maharashtra in FY24-25 and FY 2025-26",
        "Compare MS and HSD in ECZ zone",
        "LPG sales in Mumbai region for APR",
    ]

    for q in test_queries:
        print(f"\nQ: {q}")
        entities = extractor.extract(q)
        plan = planner.plan(entities)
        print("Planned Filters:")
        import json
        print(json.dumps(plan, indent=2))
