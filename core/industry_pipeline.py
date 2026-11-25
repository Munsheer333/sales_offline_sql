"""
NLPSQL SYSTEM — INDUSTRY PERFORMANCE PIPELINE
=============================================
Five-stage deterministic pipeline for industry_performance table queries.

Stages:
  1. Entity Extraction         — identify what the user mentioned
  2. Conditional Filter Planner— decide which WHERE clauses to add
  3. Business Rule Engine      — apply mandatory rules (category, SBU exclusions, etc.)
  4. Query Planner             — determine SELECT / GROUP BY / ORDER BY / LIMIT
  5. SQL Generator             — render final PostgreSQL string + parameterized form

Usage:
    from core.industry_pipeline import IndustryPipeline
    result = IndustryPipeline().run("Show HPCL sales in Maharashtra for FY 2024-2025")
    print(result.sql)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from config import CURRENT_FINANCIAL_YEAR
from core.metadata_discovery import load_entities

# ── Helpers ───────────────────────────────────────────────────────────────────
_MONTH_ORDER = {
    "APR": 1, "MAY": 2, "JUN": 3, "JUL": 4, "AUG": 5, "SEP": 6,
    "OCT": 7, "NOV": 8, "DEC": 9, "JAN": 10, "FEB": 11, "MAR": 12,
}


def _norm(text: str) -> str:
    return text.upper().strip()


def _literal(v: Any) -> str:
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 1 — ENTITY EXTRACTION
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class ExtractedEntities:
    companies:    List[str] = field(default_factory=list)
    sbus:         List[str] = field(default_factory=list)
    products:     List[str] = field(default_factory=list)
    states:       List[str] = field(default_factory=list)
    districts:    List[str] = field(default_factory=list)
    zones:        List[str] = field(default_factory=list)
    regions:      List[str] = field(default_factory=list)
    fiscal_years: List[str] = field(default_factory=list)
    months:       List[str] = field(default_factory=list)
    # Aggregation signals
    wants_groupby: List[str] = field(default_factory=list)   # dimension names
    metric:        str = "actual_sales"
    limit:         Optional[int] = None
    order_dir:     str = "DESC"


class EntityExtractor:
    """
    Stage 1: Extract all entities from a natural language question.
    Compares every token against the live entity catalog from metadata/entities.json.
    Never hardcodes entity values — everything comes from the DB.
    """

    def __init__(self):
        catalog = load_entities(auto_refresh=True)
        self._companies   = {_norm(v) for v in catalog.get("companies", [])}
        self._sbus        = {_norm(v) for v in catalog.get("sbus", [])}
        self._products    = {_norm(v) for v in catalog.get("products", [])}
        self._states      = {_norm(v) for v in catalog.get("states", [])}
        self._districts   = {_norm(v) for v in catalog.get("districts", [])}
        self._zones       = {_norm(v) for v in catalog.get("zones", [])}
        self._regions     = {_norm(v) for v in catalog.get("regions", [])}
        self._fiscal_years= {_norm(v) for v in catalog.get("fiscal_years", [])}

    def _match_by_scan(self, question_upper: str, catalog: Set[str]) -> List[str]:
        """Match full entity values that appear as substrings in the question."""
        found = []
        for val in catalog:
            if val and re.search(rf"\b{re.escape(val)}\b", question_upper):
                found.append(val)
        return sorted(found)

    def extract(self, question: str) -> ExtractedEntities:
        q = question.strip()
        q_up = _norm(q)
        tokens = re.split(r"[\s,/]+", q_up)
        bigrams = [f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens) - 1)]
        trigrams = [f"{tokens[i]} {tokens[i+1]} {tokens[i+2]}" for i in range(len(tokens) - 2)]
        candidates = set(tokens) | set(bigrams) | set(trigrams)

        e = ExtractedEntities()

        # ── Token-based matching (fast, for short values like HPCL, LPG) ───
        e.companies    = sorted(candidates & self._companies)
        e.sbus         = sorted(candidates & self._sbus)
        e.products     = sorted(candidates & self._products)
        e.fiscal_years = sorted(candidates & self._fiscal_years)

        # ── Substring scan for multi-word values (states, districts, zones, regions) ──
        e.states    = self._match_by_scan(q_up, self._states)
        e.districts = self._match_by_scan(q_up, self._districts)
        e.zones     = self._match_by_scan(q_up, self._zones)
        e.regions   = self._match_by_scan(q_up, self._regions)

        # ── Fiscal year pattern (e.g. "FY 2024-25", "2024-2025") ────────────
        fy_matches = re.findall(r"\b(20\d{2}[-–]\d{2,4})\b", q)
        for fy in fy_matches:
            # normalise short form 2024-25 → 2024-2025
            parts = re.split(r"[-–]", fy)
            if len(parts) == 2 and len(parts[1]) == 2:
                fy = f"{parts[0]}-20{parts[1]}"
            if fy not in e.fiscal_years:
                e.fiscal_years.append(fy)

        # ── Month names ──────────────────────────────────────────────────────
        for m in _MONTH_ORDER:
            if re.search(rf"\b{m}\b", q_up):
                e.months.append(m)

        # ── Aggregation signals ──────────────────────────────────────────────
        if re.search(r"\b(by company|per company|each company|company[-\s]?wise)\b", q_up):
            e.wants_groupby.append("company")
        if re.search(r"\b(by state|per state|each state|state[-\s]?wise)\b", q_up):
            e.wants_groupby.append("state")
        if re.search(r"\b(by sbu|per sbu|each sbu|sbu[-\s]?wise)\b", q_up):
            e.wants_groupby.append("sbu")
        if re.search(r"\b(by product|per product|each product|product[-\s]?wise)\b", q_up):
            e.wants_groupby.append("product")
        if re.search(r"\b(by zone|per zone|each zone|zone[-\s]?wise)\b", q_up):
            e.wants_groupby.append("zone")
        if re.search(r"\b(by district|per district|each district|district[-\s]?wise)\b", q_up):
            e.wants_groupby.append("district")
        if re.search(r"\b(by month|per month|each month|monthly|month[-\s]?wise)\b", q_up):
            e.wants_groupby.append("month")
        if re.search(r"\b(by region|per region|each region|region[-\s]?wise)\b", q_up):
            e.wants_groupby.append("region")

        # ── Ordering signals ─────────────────────────────────────────────────
        if re.search(r"\b(lowest|least|minimum|min|bottom|worst)\b", q_up):
            e.order_dir = "ASC"

        # ── LIMIT signals ────────────────────────────────────────────────────
        m_limit = re.search(r"\b(?:top|bottom|first|last)\s+(\d{1,3})\b", q_up)
        if m_limit:
            e.limit = min(int(m_limit.group(1)), 500)

        return e


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 2 — CONDITIONAL FILTER PLANNER
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class FilterPlan:
    """Each entry is (column, operator, value_or_list)."""
    conditions: List[Tuple[str, str, Any]] = field(default_factory=list)
    params: List[Any] = field(default_factory=list)

    def add(self, column: str, operator: str, value: Any):
        self.conditions.append((column, operator, value))
        if operator == "IN":
            self.params.extend(value)
        else:
            self.params.append(value)


class ConditionalFilterPlanner:
    """
    Stage 2: Add a filter ONLY when an entity was explicitly detected.
    Never adds filters for entities that were not mentioned.
    """

    def plan(self, entities: ExtractedEntities) -> FilterPlan:
        fp = FilterPlan()

        # Single-value filters
        if len(entities.companies) == 1:
            fp.add("comname", "=", entities.companies[0])
        elif len(entities.companies) > 1:
            fp.add("comname", "IN", entities.companies)

        if len(entities.sbus) == 1:
            fp.add("sbu_name", "=", entities.sbus[0])
        elif len(entities.sbus) > 1:
            fp.add("sbu_name", "IN", entities.sbus)

        if len(entities.products) == 1:
            fp.add("productname", "=", entities.products[0])
        elif len(entities.products) > 1:
            fp.add("productname", "IN", entities.products)

        if len(entities.states) == 1:
            fp.add("statename", "=", entities.states[0])
        elif len(entities.states) > 1:
            fp.add("statename", "IN", entities.states)

        if len(entities.districts) == 1:
            fp.add("distname", "=", entities.districts[0])
        elif len(entities.districts) > 1:
            fp.add("distname", "IN", entities.districts)

        if len(entities.zones) == 1:
            fp.add("zone_name", "=", entities.zones[0])
        elif len(entities.zones) > 1:
            fp.add("zone_name", "IN", entities.zones)

        if len(entities.regions) == 1:
            fp.add("ro", "=", entities.regions[0])
        elif len(entities.regions) > 1:
            fp.add("ro", "IN", entities.regions)

        if len(entities.fiscal_years) == 1:
            fp.add("fiscal_year", "=", entities.fiscal_years[0])
        elif len(entities.fiscal_years) > 1:
            fp.add("fiscal_year", "IN", entities.fiscal_years)

        if len(entities.months) == 1:
            fp.add("month_name", "=", entities.months[0])
        elif len(entities.months) > 1:
            fp.add("month_name", "IN", entities.months)

        return fp


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 3 — BUSINESS RULE ENGINE
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class BusinessRules:
    mandatory_where: List[str] = field(default_factory=list)   # raw SQL snippets
    default_fiscal_year: Optional[str] = None


class BusinessRuleEngine:
    """
    Stage 3: Apply mandatory, non-negotiable business rules from the schema file.

    Rules from industry_performance_schema_extracted.txt:
      - ALWAYS exclude category = 'O'  (category != 'O' OR category IS NULL)
      - If no fiscal year was detected → default to CURRENT_FINANCIAL_YEAR
    """

    def apply(self, entities: ExtractedEntities, filter_plan: FilterPlan) -> BusinessRules:
        rules = BusinessRules()

        # Rule 1: MANDATORY category exclusion
        rules.mandatory_where.append("(category != 'O' OR category IS NULL)")

        # Rule 2: Default fiscal year when none is detected
        if not entities.fiscal_years:
            rules.default_fiscal_year = CURRENT_FINANCIAL_YEAR
            filter_plan.add("fiscal_year", "=", CURRENT_FINANCIAL_YEAR)

        return rules


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 4 — QUERY PLANNER
# ═════════════════════════════════════════════════════════════════════════════

_GROUPBY_COLUMN_MAP = {
    "company":  "comname",
    "state":    "statename",
    "sbu":      "sbu_name",
    "product":  "productname",
    "zone":     "zone_name",
    "district": "distname",
    "month":    "month_name",
    "region":   "ro",
}

_ORDERBY_MONTH_CASE = """CASE month_name
    WHEN 'APR' THEN 1 WHEN 'MAY' THEN 2 WHEN 'JUN' THEN 3
    WHEN 'JUL' THEN 4 WHEN 'AUG' THEN 5 WHEN 'SEP' THEN 6
    WHEN 'OCT' THEN 7 WHEN 'NOV' THEN 8 WHEN 'DEC' THEN 9
    WHEN 'JAN' THEN 10 WHEN 'FEB' THEN 11 WHEN 'MAR' THEN 12
  END"""


@dataclass
class QueryPlan:
    select_columns: List[str] = field(default_factory=list)   # dimension columns
    metric_expr: str = "ROUND(SUM(netweight_tmt)::numeric, 2) AS actual_sales_tmt"
    group_by: List[str] = field(default_factory=list)
    order_by: str = ""
    limit: Optional[int] = None
    is_aggregate: bool = True


class QueryPlanner:
    """
    Stage 4: Decide SELECT columns, GROUP BY, ORDER BY, and LIMIT.
    """

    def plan(self, entities: ExtractedEntities) -> QueryPlan:
        qp = QueryPlan()
        qp.limit = entities.limit

        # Determine GROUP BY from explicit groupby signals OR from detected multi-entity context
        groupby_dims: List[str] = list(entities.wants_groupby)

        # Auto-infer groupby: if multiple companies / states detected, group by them
        if not groupby_dims:
            if len(entities.companies) > 1:
                groupby_dims.append("company")
            if len(entities.states) > 1:
                groupby_dims.append("state")
            if len(entities.sbus) > 1:
                groupby_dims.append("sbu")
            if len(entities.products) > 1:
                groupby_dims.append("product")

        for dim in groupby_dims:
            col = _GROUPBY_COLUMN_MAP.get(dim)
            if col and col not in qp.select_columns:
                qp.select_columns.append(col)
                qp.group_by.append(col)

        # ORDER BY: month has a special ordering, others use the metric
        if "month" in groupby_dims:
            qp.order_by = _ORDERBY_MONTH_CASE
        elif groupby_dims:
            qp.order_by = f"actual_sales_tmt {entities.order_dir}"

        qp.is_aggregate = True  # always aggregate on industry_performance
        return qp


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 5 — SQL GENERATOR
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class GeneratedSQL:
    sql: str
    parameterized_sql: str
    params: List[Any]
    entities: ExtractedEntities
    filter_plan: FilterPlan
    query_plan: QueryPlan
    business_rules: BusinessRules


class SQLGenerator:
    """
    Stage 5: Render the final PostgreSQL SQL from the query plan + filter plan.
    """

    def generate(
        self,
        query_plan: QueryPlan,
        filter_plan: FilterPlan,
        business_rules: BusinessRules,
    ) -> Tuple[str, str]:
        # SELECT clause
        dim_cols = ", ".join(query_plan.select_columns)
        if dim_cols:
            select_clause = f"{dim_cols}, {query_plan.metric_expr}"
        else:
            select_clause = query_plan.metric_expr

        # WHERE clause — mandatory rules first, then conditional filters
        where_parts_raw: List[str] = list(business_rules.mandatory_where)
        where_parts_param: List[str] = list(business_rules.mandatory_where)

        for col, op, val in filter_plan.conditions:
            if op == "IN":
                placeholders = ", ".join(_literal(v) for v in val)
                param_placeholders = ", ".join("%s" for _ in val)
                where_parts_raw.append(f"{col} IN ({placeholders})")
                where_parts_param.append(f"{col} IN ({param_placeholders})")
            else:
                where_parts_raw.append(f"{col} {op} {_literal(val)}")
                where_parts_param.append(f"{col} {op} %s")

        where_sql = "\n  AND ".join(where_parts_raw)
        where_param = "\n  AND ".join(where_parts_param)

        # GROUP BY clause
        group_sql = ""
        if query_plan.group_by:
            group_sql = "\nGROUP BY " + ", ".join(query_plan.group_by)

        # ORDER BY clause
        order_sql = ""
        if query_plan.order_by:
            order_sql = "\nORDER BY " + query_plan.order_by

        # LIMIT clause
        limit_sql = ""
        if query_plan.limit:
            limit_sql = f"\nLIMIT {query_plan.limit}"

        sql = (
            f"SELECT {select_clause}\n"
            f"FROM industry_performance\n"
            f"WHERE {where_sql}"
            f"{group_sql}"
            f"{order_sql}"
            f"{limit_sql};"
        )

        parameterized_sql = (
            f"SELECT {select_clause}\n"
            f"FROM industry_performance\n"
            f"WHERE {where_param}"
            f"{group_sql}"
            f"{order_sql}"
            f"{limit_sql};"
        )

        return sql, parameterized_sql


# ═════════════════════════════════════════════════════════════════════════════
# PIPELINE RUNNER
# ═════════════════════════════════════════════════════════════════════════════

class IndustryPipeline:
    """
    Runs all five stages in sequence and returns a GeneratedSQL result.

    Example:
        result = IndustryPipeline().run("Show HPCL sales in Maharashtra")
        print(result.sql)
    """

    def __init__(self):
        self._extractor = EntityExtractor()
        self._filter_planner = ConditionalFilterPlanner()
        self._rule_engine = BusinessRuleEngine()
        self._query_planner = QueryPlanner()
        self._sql_generator = SQLGenerator()

    def run(self, question: str) -> GeneratedSQL:
        # Stage 1
        entities = self._extractor.extract(question)
        # Stage 2
        filter_plan = self._filter_planner.plan(entities)
        # Stage 3
        rules = self._rule_engine.apply(entities, filter_plan)
        # Stage 4
        query_plan = self._query_planner.plan(entities)
        # Stage 5
        sql, parameterized_sql = self._sql_generator.generate(query_plan, filter_plan, rules)

        return GeneratedSQL(
            sql=sql,
            parameterized_sql=parameterized_sql,
            params=filter_plan.params,
            entities=entities,
            filter_plan=filter_plan,
            query_plan=query_plan,
            business_rules=rules,
        )


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    test_questions = [
        "Show HPCL sales in Maharashtra for FY 2024-2025",
        "Sales of HSD product in RETAIL SBU",
        "Top 5 companies by sales in ECZ zone",
        "Monthly sales trend for BPCL in FY 2024-2025",
        "Show all company sales",
    ]
    pipeline = IndustryPipeline()
    for q in test_questions:
        print(f"\n{'='*60}")
        print(f"Q: {q}")
        result = pipeline.run(q)
        print(f"SQL:\n{result.sql}")
        print(f"Params: {result.params}")
