"""Question-to-SQL semantic coverage checks for execution gating."""

import re
from typing import Any, Dict, List, Optional

import sqlglot
from sqlglot import exp

from core.phase3_query_planner import generate_logical_plan


def validate_question_sql_coverage(
    question: str,
    sql: str,
    schema_metadata: dict,
    logical_plan: Optional[dict] = None,
    entity_analysis: Optional[dict] = None,
) -> Dict[str, Any]:
    """Reject syntactically valid SQL that silently drops question obligations."""
    entity_analysis = entity_analysis or {"matches": [], "unresolved": []}
    plan = logical_plan or generate_logical_plan(
        question,
        schema_metadata=schema_metadata,
        entity_matches=entity_analysis.get("matches"),
    )
    report = {
        "valid": True,
        "errors": [],
        "expected_filters": [],
        "expected_group_by": [],
        "expected_limit": plan.get("limit"),
        "expected_order_by": plan.get("order_by", []),
    }
    for unresolved in entity_analysis.get("unresolved", []):
        _error(report, "UNRESOLVED_ENTITY", f"Unknown {unresolved['type']}: {unresolved['value']}")

    try:
        root = sqlglot.parse_one(sql, read="postgres")
    except Exception as exc:
        _error(report, "SQL_PARSE_FAILED", f"Cannot parse SQL for semantic coverage: {exc}")
        return report

    for item in plan.get("filters", []):
        if item.get("is_business_rule"):
            continue
        expected = {
            "column": item.get("column"),
            "operator": item.get("operator"),
            "value": item.get("value"),
            "source": item.get("source"),
        }
        report["expected_filters"].append(expected)
        if not _filter_satisfied(root, expected):
            _error(
                report,
                "MISSING_FILTER",
                f"SQL dropped required filter {expected['column']} {expected['operator']} {expected['value']!r}",
            )

    group_columns = {_identifier(column.name) for group in root.find_all(exp.Group) for column in group.find_all(exp.Column)}
    select_columns = {
        _identifier(column.name)
        for select in root.find_all(exp.Select)
        for projection in select.expressions
        for column in projection.find_all(exp.Column)
    }
    for item in plan.get("group_by", []):
        column = _identifier(item.get("column"))
        report["expected_group_by"].append(column)
        if column not in group_columns:
            _error(report, "MISSING_GROUP_BY", f"SQL dropped required GROUP BY column: {column}")
        if column not in select_columns:
            _error(report, "MISSING_DIMENSION_PROJECTION", f"SQL dropped requested output dimension: {column}")

    expected_limit = plan.get("limit")
    if expected_limit is not None and _limit_value(root) != int(expected_limit):
        _error(report, "MISSING_LIMIT", f"SQL must use LIMIT {expected_limit}")

    if plan.get("order_by") and not any(True for _node in root.find_all(exp.Ordered)):
        _error(report, "MISSING_ORDER_BY", "SQL dropped requested ordering")

    q_norm = re.sub(r"\s+", " ", (question or "").lower())
    if "market share" in q_norm and _cte_filters_column(root, "total_market", "comname"):
        _error(
            report,
            "COMPANY_FILTER_IN_MARKET_DENOMINATOR",
            "Market-share denominator must include total market sales, not a single company's sales",
        )

    if (
        any(term in q_norm for term in ["growth", "difference", "year over year", "year-on-year", "yoy"])
        or _is_product_growth_threshold_question(q_norm)
    ):
        aliases = {_identifier(alias.alias) for alias in root.find_all(exp.Alias)}
        for alias in ["sales_difference_tmt", "sales_growth_percentage"]:
            if alias == "sales_difference_tmt" and _is_product_growth_threshold_question(q_norm):
                continue
            if alias not in aliases:
                _error(report, "MISSING_COMPARISON_OUTPUT", f"SQL dropped required comparison output: {alias}")

    if _is_product_growth_threshold_question(q_norm) and not root.find(exp.Having):
        _error(report, "MISSING_GROWTH_THRESHOLD", "SQL dropped the requested product growth percentage threshold")

    # Validate that ratio/contribution questions actually calculate a ratio/percentage
    if any(term in q_norm for term in ["market share", "market contribution", "contributed", "contribution"]):
        aliases = {_identifier(alias.alias) for alias in root.find_all(exp.Alias)}
        has_ratio_alias = any("share" in alias or "contribution" in alias or "percentage" in alias or "contrib" in alias for alias in aliases)
        has_division = "/" in sql or any(isinstance(node, exp.Div) for node in root.find_all(exp.Div))
        if not (has_ratio_alias or has_division):
            _error(
                report,
                "MISSING_RATIO_CALCULATION",
                "SQL dropped the requested market share or contribution percentage calculation",
            )

    # Validate that "declining/growing" queries compare at least two time periods
    if any(term in q_norm for term in ["declining", "decline", "falling", "dropping", "growing states", "negative growth"]):
        has_case_when = "CASE WHEN" in sql.upper() or "CASE\nWHEN" in sql.upper()
        has_two_periods = len(re.findall(r"fiscal_year\s*(?:=|IN|in)", sql, re.IGNORECASE)) >= 2 or has_case_when
        if not has_two_periods:
            _error(
                report,
                "MISSING_TEMPORAL_COMPARISON",
                "SQL dropped the required year-over-year comparison for decline/growth detection",
            )
        aliases = {_identifier(alias.alias) for alias in root.find_all(exp.Alias)}
        has_ratio_alias = any("share" in alias or "contribution" in alias or "percentage" in alias or "contrib" in alias for alias in aliases)
        has_division = "/" in sql or any(isinstance(node, exp.Div) for node in root.find_all(exp.Div))
        if not (has_ratio_alias or has_division):
            _error(
                report,
                "MISSING_RATIO_CALCULATION",
                "SQL dropped the requested market share or contribution percentage calculation",
            )

    # Validate percentage threshold filters (e.g., "more than 10%")
    threshold_match = re.search(r"\b(?:more than|above|exceeds|greater than|below|less than|under)\s+(\d+(?:\.\d+)?)\s*%", q_norm)
    if threshold_match:
        val = float(threshold_match.group(1))
        found_comparison = False
        for op in root.find_all(exp.Condition):
            for num in op.find_all(exp.Literal):
                try:
                    num_val = float(num.this)
                    if abs(num_val - val) < 0.01 or abs(num_val - (val / 100.0)) < 0.01:
                        found_comparison = True
                        break
                except ValueError:
                    continue
            if found_comparison:
                break
        if not found_comparison:
            # Fallback to simple regex check on the raw SQL to avoid parser variations
            raw_match = re.search(rf"(?:>|<|=|>=|<=)\s*(?:{int(val)}|{val:.1f}|{val/100.0:.2f}|{val/100.0:.1f})\b", sql)
            if not raw_match:
                _error(
                    report,
                    "MISSING_PERCENTAGE_THRESHOLD",
                    f"SQL dropped the requested percentage threshold filter ({val}%)",
                )

    return report


def _filter_satisfied(root: exp.Expression, expected: Dict[str, Any]) -> bool:
    column = _identifier(expected["column"])
    operator = expected["operator"]
    values = expected["value"] if isinstance(expected["value"], list) else [expected["value"]]
    expected_values = {_value(item) for item in values}

    if operator in {"equal", "equal_dynamic"}:
        return any(_binary_match(node, column, expected_values) for node in root.find_all(exp.EQ))
    if operator == "not_equal":
        return any(_binary_match(node, column, expected_values) for node in root.find_all(exp.NEQ))
    if operator in {"in", "not_in"}:
        return any(
            _identifier(node.this.name) == column
            and expected_values.issubset({_value(item.this) for item in node.expressions if isinstance(item, exp.Literal)})
            for node in root.find_all(exp.In)
            if isinstance(node.this, exp.Column)
        )
    if operator == "between":
        return any(
            isinstance(node.this, exp.Column) and _identifier(node.this.name) == column
            for node in root.find_all(exp.Between)
        )
    return True


def _binary_match(node: exp.Expression, column: str, expected_values: set) -> bool:
    pairs = ((node.left, node.right), (node.right, node.left))
    return any(
        _column_name(left) == column
        and _literal_value(right) in expected_values
        for left, right in pairs
    )


def _column_name(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Column):
        return _identifier(node.name)
    if isinstance(node, exp.Upper):
        return _column_name(node.this)
    return None


def _literal_value(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal):
        return _value(node.this)
    if isinstance(node, exp.Upper):
        return _literal_value(node.this)
    return None


def _limit_value(root: exp.Expression) -> Optional[int]:
    limit = root.args.get("limit")
    if not limit or not isinstance(limit.expression, exp.Literal):
        return None
    try:
        return int(limit.expression.this)
    except (TypeError, ValueError):
        return None


def _cte_filters_column(root: exp.Expression, cte_name: str, column_name: str) -> bool:
    for cte in root.find_all(exp.CTE):
        if _identifier(cte.alias_or_name) != _identifier(cte_name):
            continue
        where = cte.this.find(exp.Where)
        return bool(
            where
            and any(_identifier(column.name) == _identifier(column_name) for column in where.find_all(exp.Column))
        )
    return False


def _identifier(value: Any) -> str:
    return str(value or "").strip().lower()


def _value(value: Any) -> str:
    return str(value).strip().casefold()


def _is_product_growth_threshold_question(q_norm: str) -> bool:
    return (
        ("product" in q_norm or "products" in q_norm)
        and "%" in q_norm
        and any(term in q_norm for term in ["exceed", "more than", "greater than", "above", "over", "below", "under", "at least", "at most"])
    )


def _error(report: Dict[str, Any], code: str, message: str) -> None:
    report["valid"] = False
    report["errors"].append({"code": code, "message": message})
