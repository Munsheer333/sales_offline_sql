"""Central business formula registry and SQL render helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Tuple


class BusinessFormulaError(ValueError):
    """Raised when a business formula cannot be resolved or rendered."""


@dataclass(frozen=True)
class BusinessFormula:
    name: str
    label: str
    description: str
    expression_template: str
    required_inputs: Tuple[str, ...]
    default_alias: str
    output_unit: str
    aliases: Tuple[str, ...] = ()

    def render(self, **inputs: str) -> str:
        missing = [key for key in self.required_inputs if not inputs.get(key)]
        if missing:
            raise BusinessFormulaError(
                f"Formula {self.name!r} is missing required input(s): {', '.join(missing)}"
            )
        return self.expression_template.format(**inputs)


class BusinessFormulaEngine:
    def __init__(self, formulas: Iterable[BusinessFormula]):
        self._formulas: Dict[str, BusinessFormula] = {}
        self._aliases: Dict[str, str] = {}
        for formula in formulas:
            self._formulas[formula.name] = formula
            for alias in (formula.name, formula.label, *formula.aliases):
                self._aliases[_normalize(alias)] = formula.name

    def list_formulas(self) -> Tuple[BusinessFormula, ...]:
        return tuple(self._formulas[name] for name in sorted(self._formulas))

    def get(self, name: str) -> BusinessFormula:
        key = self._aliases.get(_normalize(name))
        if not key:
            raise BusinessFormulaError(f"Unknown business formula: {name!r}")
        return self._formulas[key]

    def render(self, name: str, **inputs: str) -> str:
        return self.get(name).render(**inputs)

    def actual_volume_sql(
        self,
        measure: str,
        *,
        aggregate: str = "SUM",
        unit: str = "TMT",
        alias: Optional[str] = None,
        round_result: bool = False,
    ) -> str:
        expression = f"{aggregate.upper()}({measure})"
        if unit.upper() == "MT":
            expression = f"{expression} * 1000"
        if round_result:
            expression = f"ROUND({expression}::numeric, 2)"
        return _with_alias(expression, alias)

    def market_share_sql(self, numerator: str, denominator: str, *, alias: Optional[str] = None) -> str:
        expression = self.render("market_share_percentage", numerator=numerator, denominator=denominator)
        return _with_alias(expression, alias)

    def sales_difference_sql(self, current: str, previous: str, *, alias: Optional[str] = None) -> str:
        expression = self.render("sales_difference", current=current, previous=previous)
        return _with_alias(expression, alias)

    def growth_percentage_sql(self, current: str, previous: str, *, alias: Optional[str] = None) -> str:
        expression = self.render("growth_percentage", current=current, previous=previous)
        return _with_alias(expression, alias)

    def fiscal_year_total_sql(self, table: str, measure_column: str, fiscal_year: str) -> str:
        measure = f"{table}.{measure_column}"
        fiscal_year_column = f"{table}.fiscal_year"
        return self.render(
            "fiscal_year_total",
            fiscal_year_column=fiscal_year_column,
            fiscal_year=fiscal_year,
            measure=measure,
        )

    def metadata(self, name: str) -> Mapping[str, object]:
        formula = self.get(name)
        return {
            "name": formula.name,
            "label": formula.label,
            "description": formula.description,
            "expression_template": formula.expression_template,
            "required_inputs": list(formula.required_inputs),
            "default_alias": formula.default_alias,
            "output_unit": formula.output_unit,
            "aliases": list(formula.aliases),
        }


def _with_alias(expression: str, alias: Optional[str]) -> str:
    return f"{expression} AS {alias}" if alias else expression


def _normalize(value: str) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


BUSINESS_FORMULAS: Tuple[BusinessFormula, ...] = (
    BusinessFormula(
        name="market_share_percentage",
        label="Market Share",
        description="Company or segment sales as a percentage of the matching total market sales.",
        expression_template="ROUND(({numerator} * 100.0) / NULLIF({denominator}, 0), 2)",
        required_inputs=("numerator", "denominator"),
        default_alias="market_share_percentage",
        output_unit="percentage",
        aliases=("market share", "market contribution"),
    ),
    BusinessFormula(
        name="growth_percentage",
        label="Growth %",
        description="Percentage change from a previous value to a current value.",
        expression_template=(
            "ROUND(CASE WHEN {previous} = 0 THEN NULL "
            "ELSE (({current} - {previous}) * 100.0 / {previous})::numeric END, 2)"
        ),
        required_inputs=("current", "previous"),
        default_alias="sales_growth_percentage",
        output_unit="percentage",
        aliases=("growth", "growth %", "sales growth"),
    ),
    BusinessFormula(
        name="sales_difference",
        label="Sales Difference",
        description="Absolute difference between current and previous sales values.",
        expression_template="ROUND(({current} - {previous})::numeric, 2)",
        required_inputs=("current", "previous"),
        default_alias="sales_difference_tmt",
        output_unit="TMT",
        aliases=("difference", "sales variance"),
    ),
    BusinessFormula(
        name="actual_volume",
        label="Actual Volume",
        description="Aggregated actual sales volume, defaulting to TMT unless converted to MT.",
        expression_template="{aggregate}({measure})",
        required_inputs=("aggregate", "measure"),
        default_alias="sum_actual_sales_tmt",
        output_unit="TMT",
        aliases=("actual sales", "sales volume", "volume sold"),
    ),
    BusinessFormula(
        name="fiscal_year_total",
        label="Fiscal Year Total",
        description="Conditional total for one fiscal year inside a comparison query.",
        expression_template=(
            "COALESCE(SUM(CASE WHEN {fiscal_year_column} = {fiscal_year} "
            "THEN {measure} ELSE 0 END), 0)"
        ),
        required_inputs=("fiscal_year_column", "fiscal_year", "measure"),
        default_alias="fiscal_year_sales_tmt",
        output_unit="TMT",
        aliases=("year total", "yearly total"),
    ),
)


FORMULA_ENGINE = BusinessFormulaEngine(BUSINESS_FORMULAS)
