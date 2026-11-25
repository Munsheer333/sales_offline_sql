"""Generated offline coverage benchmark for deterministic analytics patterns."""

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence

from core.business_entity_catalog import analyze_business_entities
from core.db_pool import pooled_connect
from core.phase4_validator import validate_and_execute
from core.phase4_semantic_coverage import validate_question_sql_coverage
from core.phase6_query_pattern_engine import match_query_pattern


def load_coverage_catalog(conn_str: str) -> Dict[str, List[str]]:
    """Read distinct business values from the current local datasets."""
    queries = {
        "industry_companies": "SELECT DISTINCT comname FROM industry_performance WHERE comname IS NOT NULL",
        "industry_sbus": "SELECT DISTINCT sbu_name FROM industry_performance WHERE sbu_name IS NOT NULL",
        "industry_products": "SELECT DISTINCT productname FROM industry_performance WHERE productname IS NOT NULL",
        "industry_zones": "SELECT DISTINCT zone_name FROM industry_performance WHERE zone_name IS NOT NULL",
        "industry_years": "SELECT DISTINCT fiscal_year FROM industry_performance WHERE fiscal_year IS NOT NULL",
        "mom_years": "SELECT DISTINCT fiscal_year FROM mom_level WHERE fiscal_year IS NOT NULL",
        "m60_sbus": "SELECT DISTINCT sbu_name FROM m60_level WHERE sbu_name IS NOT NULL",
        "m60_products": "SELECT DISTINCT productname FROM m60_level WHERE productname IS NOT NULL",
        "m60_zones": "SELECT DISTINCT zone_name FROM m60_level WHERE zone_name IS NOT NULL",
    }
    catalog: Dict[str, List[str]] = {}
    with pooled_connect(conn_str) as conn:
        cur = conn.cursor()
        for key, sql in queries.items():
            cur.execute(sql)
            catalog[key] = sorted(
                str(row[0]).strip()
                for row in cur.fetchall()
                if row and row[0] is not None and str(row[0]).strip()
            )
        cur.close()
    return catalog


def generate_coverage_cases(catalog: Dict[str, Sequence[str]]) -> List[Dict[str, str]]:
    """Generate thousands of representative questions from reusable query families."""
    companies = _clean(catalog.get("industry_companies", []))
    industry_sbus = _clean(catalog.get("industry_sbus", []))
    industry_products = _clean(catalog.get("industry_products", []))
    industry_zones = _clean(catalog.get("industry_zones", []))
    years = _clean(catalog.get("industry_years", []))
    mom_years = _clean(catalog.get("mom_years", []))
    m60_sbus = _clean(catalog.get("m60_sbus", []))
    m60_products = _clean(catalog.get("m60_products", []))
    m60_zones = _clean(catalog.get("m60_zones", []))
    year_pairs = [(previous, current) for previous, current in zip(years, years[1:])]
    mom_year_pairs = [(previous, current) for previous, current in zip(mom_years, mom_years[1:])]

    cases: List[Dict[str, str]] = []
    for company in companies:
        for year in years:
            _add(cases, "industry_company_sales", "industry_actual_sales_sum_v1",
                 f"What is the total sales for {company} in FY {year}?")
            _add(cases, "named_company_market_share", "industry_market_share_rank_v1",
                 f"What is the market share percentage of {company} in FY {year}?")
            for product in industry_products:
                _add(cases, "industry_company_product_sales", "industry_actual_sales_sum_v1",
                     f"Show total sales for {company} for product {product} in FY {year}.")

    for sbu in industry_sbus:
        for zone in industry_zones:
            for previous, current in year_pairs:
                _add(cases, "industry_sbu_zone_growth", "industry_sales_growth_comparison_v1",
                     f"Compare {sbu} SBU sales in {zone} zone for FY {current} against FY {previous} and calculate growth percentage.")

    for year in years:
        _add(cases, "company_market_share_ranking", "industry_market_share_rank_v1",
             f"Which company has the lowest market share in FY {year}?")
        _add(cases, "zone_market_contribution", "industry_market_share_rank_v1",
             f"Top 10 zones in market contribution for FY {year} for all SBUs.")

    for previous, current in mom_year_pairs:
        for threshold in [10, 20, 50]:
            _add(cases, "mom_product_growth_threshold", "mom_product_growth_threshold_v1",
                 f"Show products whose FY {current} sales exceeded FY {previous} by more than {threshold}%.")
    _add(cases, "mom_product_growth_threshold", "mom_product_growth_threshold_v1",
         "Show products whose FY 2024-2025 sales exceeded FY 2023-2024 by more than 20%.")

    for sbu in m60_sbus:
        for zone in m60_zones:
            for product in m60_products:
                _add(cases, "m60_target_quantity", "m60_target_quantity_sum_v1",
                     f"Get total target quantity in TMT from M60_LEVEL for {sbu} SBU in {_zone_label(zone)} for product {product}.")
                _add(cases, "m60_target_actual_variance", "m60_target_actual_variance_v1",
                     f"Compare total target versus actual sales from M60_LEVEL for {sbu} SBU in {_zone_label(zone)} for product {product} and show variance percentage.")

    return cases


def evaluate_pattern_coverage(
    cases: Sequence[Dict[str, str]],
    schema_metadata: dict,
    entity_catalog: Optional[Dict[str, List[str]]] = None,
    conn_str: Optional[str] = None,
    execute_limit: int = 0,
) -> Dict[str, Any]:
    """Measure deterministic matching and optionally execute a bounded local sample."""
    family_totals: Counter = Counter()
    family_passed: Counter = Counter()
    failures: List[dict] = []
    executed = 0
    execution_failed = 0
    semantic_failed = 0

    for case in cases:
        family = case["family"]
        family_totals[family] += 1
        entities = analyze_business_entities(case["question"], catalog=entity_catalog or {})
        match = match_query_pattern(
            case["question"],
            schema_metadata=schema_metadata,
            entity_matches=entities.get("matches"),
        )
        semantic_report = (
            validate_question_sql_coverage(
                case["question"],
                match["sql"],
                schema_metadata,
                entity_analysis=entities,
            )
            if match.get("matched")
            else {"valid": False, "errors": []}
        )
        passed = (
            match.get("matched")
            and match.get("template_id") == case["expected_template"]
            and semantic_report.get("valid")
        )
        if match.get("matched") and not semantic_report.get("valid"):
            semantic_failed += 1
        if passed:
            family_passed[family] += 1
        elif len(failures) < 100:
            failures.append({
                **case,
                "matched": match.get("matched", False),
                "actual_template": match.get("template_id"),
                "confidence": match.get("confidence"),
                "slots": match.get("slots", {}),
                "semantic_errors": semantic_report.get("errors", []),
            })

        if passed and conn_str and executed < execute_limit:
            executed += 1
            result = validate_and_execute(match["sql"], schema_metadata, conn_str, max_retries=1)
            if not result.get("success"):
                execution_failed += 1
                if len(failures) < 100:
                    failures.append({**case, "execution_error": result.get("error")})

    total = len(cases)
    matched = sum(family_passed.values())
    return {
        "total_cases": total,
        "matched_cases": matched,
        "failed_cases": total - matched,
        "coverage_percentage": round((matched * 100.0 / total), 2) if total else 0.0,
        "executed_sample": executed,
        "execution_failed": execution_failed,
        "semantic_failed": semantic_failed,
        "families": {
            family: {
                "total": family_totals[family],
                "matched": family_passed[family],
                "coverage_percentage": round(family_passed[family] * 100.0 / family_totals[family], 2),
            }
            for family in sorted(family_totals)
        },
        "failures": failures,
    }


def entity_catalog_for_matrix(catalog: Dict[str, Sequence[str]]) -> Dict[str, List[str]]:
    """Flatten table-specific matrix values into the runtime entity catalog shape."""
    return {
        "company": _clean(catalog.get("industry_companies", [])),
        "sbu": _clean([*catalog.get("industry_sbus", []), *catalog.get("m60_sbus", [])]),
        "product": _clean([*catalog.get("industry_products", []), *catalog.get("m60_products", [])]),
        "zone": _clean([*catalog.get("industry_zones", []), *catalog.get("m60_zones", [])]),
    }


def _add(cases: List[dict], family: str, expected_template: str, question: str) -> None:
    cases.append({"family": family, "expected_template": expected_template, "question": question})


def _clean(values: Iterable[str]) -> List[str]:
    return sorted({str(value).strip() for value in values if str(value).strip() and str(value).strip() != "-"})


def _zone_label(value: str) -> str:
    return value if value.casefold().endswith(" zone") else f"{value} zone"
