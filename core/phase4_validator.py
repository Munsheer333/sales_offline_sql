"""
NLPSQL SYSTEM — PHASE 04
Query Validation, AST Parsing & Self-Healing Engine
"""

import re
import time
import asyncio
import psycopg2
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from typing import Any, Dict, List, Tuple, Optional

from core.observability import log_event, observe_repair, observed_stage, PipelineProfiler
from core.phase3_relationship_graph import RelationshipGraphManager, RelationshipPlanningError
from core.phase4_deterministic_repair import repair_sql_deterministically
from core.db_pool import pooled_connect

SUPPORTED_AGGREGATE_TYPES = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)

SYSTEM_PROMPT_PHASE4 = """You are a PostgreSQL SQL validator and repair agent.

Given:
1. A schema (table names, column names, types)
2. A generated SQL query
3. Optionally: an error message from PostgreSQL

Perform these validation checks:
- All referenced table names exist in the schema
- All referenced column names exist in the correct tables
- Aggregate functions are paired with proper GROUP BY clauses
- JOINs reference correct key columns
- Subqueries are syntactically valid
- Window functions have proper OVER() clauses

If invalid: rewrite the corrected query.
If valid: return the query unchanged.

Output ONLY the final SQL query. Nothing else.
"""


def validate_sql_syntax(sql: str) -> Tuple[bool, str]:
    """Basic regex-level syntax checks before hitting the DB."""
    sql_upper = sql.upper().strip()
    
    if sql_upper.startswith("OFF_TOPIC"):
        return False, sql.replace("OFF_TOPIC:", "").replace("off_topic:", "").strip()
        
    if not sql_upper.startswith("SELECT"):
        if not any(sql_upper.startswith(k) for k in ["WITH", "SELECT"]):
            return False, "Query must start with SELECT or WITH (CTE)"
    
    # Check for dangerous statements
    dangerous = ["DROP ", "DELETE ", "TRUNCATE ", "UPDATE ", "INSERT ", "ALTER ", "CREATE "]
    for kw in dangerous:
        if kw in sql_upper:
            return False, f"Dangerous keyword detected: {kw.strip()}. Only SELECT queries allowed."
    
    # Check balanced parentheses
    open_p = sql.count("(")
    close_p = sql.count(")")
    if open_p != close_p:
        return False, f"Unbalanced parentheses: {open_p} open, {close_p} close"
    
    # Check for GROUP BY when aggregate functions used
    agg_pattern = r'\b(COUNT|SUM|AVG|MAX|MIN)\s*\('
    has_agg = bool(re.search(agg_pattern, sql_upper))
    has_group_by = "GROUP BY" in sql_upper
    has_window = "OVER" in sql_upper
    # note: aggregates without GROUP BY are valid (e.g. SELECT COUNT(*) FROM t)
    # so we just warn, not error
    
    return True, "Syntax pre-check passed"


def validate_table_columns(sql: str, schema_metadata: dict) -> Tuple[bool, list]:
    """Check that referenced tables and columns exist in schema."""
    known_tables = set()
    known_columns = {}
    for table in _iter_schema_tables(schema_metadata):
        table_name = _normalize_identifier(table.get("table_name"))
        if not table_name:
            continue
        known_tables.add(table_name)
        for col in table.get("columns", []):
            column_name = _column_name(col)
            if column_name:
                known_columns[f"{table_name}.{column_name}"] = True
                known_columns[column_name] = True  # unqualified

    warnings = []
    
    # 1. Identify and ignore CTE names defined in the query
    cte_pattern = r'\b(?:WITH|,)\s+(\w+)\s+AS\s*\('
    ctes = {c.lower() for c in re.findall(cte_pattern, sql, re.IGNORECASE)}
    
    # 2. Extract table references from FROM / JOIN
    from_pattern = r'\bFROM\s+([\w\.]+)|\bJOIN\s+([\w\.]+)'
    matches = re.findall(from_pattern, sql, re.IGNORECASE)
    for m in matches:
        tbl_full = (m[0] or m[1]).lower()
        if not tbl_full:
            continue
            
        # Parse potential schema prefixes (e.g. public.m60_level or information_schema.columns)
        tbl_parts = tbl_full.split('.')
        if len(tbl_parts) > 1:
            schema_prefix = tbl_parts[0]
            tbl_name = tbl_parts[1]
            if schema_prefix in ["information_schema", "pg_catalog"]:
                continue
            tbl = tbl_name
        else:
            tbl = tbl_full

        # Ignore common CTE names and system tables
        if tbl in ctes or tbl in ["information_schema", "pg_class", "pg_attribute", "pg_type", "pg_tables"]:
            continue
            
        if tbl and tbl not in known_tables:
            warnings.append(f"Unknown table: '{tbl_full}'")

    return len(warnings) == 0, warnings


def detect_grouping_dimensions(question: str) -> set:
    if not question:
        return set()
    q_lower = question.lower()
    detected = set()
    dims = {
        "company": "comname",
        "companies": "comname",
        "zone": "zone_name",
        "zones": "zone_name",
        "region": "region_name",
        "regions": "region_name",
        "sbu": "sbu_name",
        "sbus": "sbu_name",
        "state": "statename",
        "states": "statename",
        "district": "distname",
        "districts": "distname",
        "product": "productname",
        "products": "productname",
    }
    for term, col in dims.items():
        patterns = [
            r"\beach\s+" + re.escape(term) + r"\b",
            r"\bevery\s+" + re.escape(term) + r"\b",
            r"\bper\s+" + re.escape(term) + r"\b",
            r"\bby\s+" + re.escape(term) + r"\b",
            re.escape(term) + r"-wise",
            r"\bwise\s+of\s+" + re.escape(term) + r"\b",
            r"\blist\s+all\s+" + re.escape(term) + r"\b",
            r"\bshow\s+all\s+" + re.escape(term) + r"\b",
            r"\bfor\s+all\s+" + re.escape(term) + r"\b",
            r"\bfind\s+" + re.escape(term) + r"\b",
            r"\bshow\s+" + re.escape(term) + r"\b",
            r"\bwhich\s+" + re.escape(term) + r"\b",
            r"\brank\s+of\s+(?:each\s+)?" + re.escape(term) + r"\b",
        ]
        if term in ["companies", "zones", "regions", "sbus", "states", "districts", "products"]:
            patterns.append(r"\b" + re.escape(term) + r"\b")
        for pattern in patterns:
            if re.search(pattern, q_lower):
                detected.add(col)
                break
    # Exclude sbu_name if "for all sbus" or similar is specified
    if "for all sbu" in q_lower:
        detected.discard("sbu_name")
    return detected


def _validate_dimension_group_by(root: exp.Expression, catalog: Dict[str, set], question: str, report: Dict[str, Any]) -> None:
    required_group_cols = detect_grouping_dimensions(question)
    if not required_group_cols:
        return

    all_selects = list(root.find_all(exp.Select))
    
    # 1. Collect all physical tables accessed in the entire AST
    all_physical_tables = set()
    for select in all_selects:
        for table in select.find_all(exp.Table):
            t_name = table.name.lower()
            if t_name in catalog:
                all_physical_tables.add(t_name)

    # If no physical tables are found, we cannot validate columns
    if not all_physical_tables:
        return

    for req_col in required_group_cols:
        # Check if the required column is present in any of the physical tables accessed
        has_col_in_any_table = any(req_col in catalog.get(t, set()) for t in all_physical_tables)
        
        if not has_col_in_any_table:
            # The query does not access any physical table containing this dimension
            _add_error(
                report,
                "group_by",
                f"Required dimension column '{req_col}' (implied by question: '{question}') is not present in any of the queried tables: {list(all_physical_tables)}.",
                f"Query a table that contains '{req_col}' (e.g., industry_performance for state/district/company, or mom_level/m60_level for region/zone/SBU/product) and GROUP BY '{req_col}'.",
            )
            continue

        # 2. Check if at least one select block groups by the required column
        has_group_by = False
        for select in all_selects:
            select_tables = [table.name.lower() for table in select.find_all(exp.Table)]
            if any(req_col in catalog.get(t, set()) for t in select_tables if t in catalog):
                group = select.args.get("group")
                if group:
                    group_cols = set()
                    for expr in group.expressions:
                        for col in expr.find_all(exp.Column):
                            group_cols.add(col.name.lower())
                    if req_col in group_cols:
                        has_group_by = True
                        break
        
        if not has_group_by:
            _add_error(
                report,
                "group_by",
                f"Missing mandatory GROUP BY on '{req_col}' implied by question: '{question}'",
                f"Add GROUP BY {req_col} to partition results by dimension.",
            )


def validate_sql_ast(sql: str, schema_metadata: dict, question: Optional[str] = None) -> Dict[str, Any]:
    """Compiler-grade PostgreSQL AST validation using sqlglot."""
    report = _empty_validation_report(sql)

    if sql.strip().upper().startswith("OFF_TOPIC"):
        return report

    try:
        statements = sqlglot.parse(sql, read="postgres")
    except ParseError as exc:
        _add_error(report, "parse", f"SQL parse failed: {exc}", "Return valid PostgreSQL SELECT syntax only.")
        return _finalize_report(report)

    statements = [statement for statement in statements if statement is not None]
    if len(statements) != 1:
        _add_error(
            report,
            "statement_count",
            "Exactly one SQL statement is allowed.",
            "Remove extra statements and return one SELECT or WITH query.",
        )
        return _finalize_report(report)

    root = statements[0]
    report["statement_type"] = root.key.upper()

    if _contains_disallowed_statement(root):
        _add_error(
            report,
            "statement_type",
            "Disallowed SQL statement detected. Only SELECT/WITH queries are allowed.",
            "Rewrite the query as a read-only SELECT query.",
        )
        return _finalize_report(report)

    if not isinstance(root, (exp.Select, exp.Union)):
        _add_error(
            report,
            "statement_type",
            f"Unsupported SQL statement type: {root.key.upper()}",
            "Return a SELECT query only.",
        )
        return _finalize_report(report)

    catalog = _build_schema_catalog(schema_metadata)
    cte_aliases = _cte_aliases(root)
    subquery_aliases = _subquery_aliases(root)
    table_aliases, table_errors = _table_aliases(root, catalog, cte_aliases, subquery_aliases)
    for err in table_errors:
        _add_error(report, "tables", err, "Use only table names present in the schema metadata.")

    report["tables"] = sorted(set(table_aliases.values()))
    report["aliases"]["tables"] = dict(sorted(table_aliases.items()))
    report["aliases"]["ctes"] = sorted(cte_aliases)
    report["aliases"]["subqueries"] = sorted(subquery_aliases)

    _validate_columns(root, catalog, table_aliases, cte_aliases, subquery_aliases, report)
    _validate_joins(root, catalog, table_aliases, cte_aliases, subquery_aliases, schema_metadata, report)
    _validate_aggregations(root, table_aliases, report)
    _validate_aliases(root, catalog, table_aliases, cte_aliases, subquery_aliases, report)
    if question:
        _validate_dimension_group_by(root, catalog, question, report)

    return _finalize_report(report)


def validate_generated_sql(sql: str, schema_metadata: dict, question: Optional[str] = None) -> Dict[str, Any]:
    """Validate generated PostgreSQL SQL and return a rejection report when invalid."""
    syntax_ok, syntax_msg = validate_sql_syntax(sql)
    if not syntax_ok:
        report = _empty_validation_report(sql)
        _add_error(report, "syntax", syntax_msg, "Return one read-only PostgreSQL SELECT or WITH query.")
        return _finalize_report(report)
    return validate_sql_ast(sql, schema_metadata, question=question)


async def validate_sql_ast_async(sql: str, schema_metadata: dict, question: Optional[str] = None) -> Dict[str, Any]:
    return await asyncio.to_thread(validate_sql_ast, sql, schema_metadata, question)


def _empty_validation_report(sql: str) -> Dict[str, Any]:
    return {
        "valid": True,
        "sql": sql,
        "dialect": "postgres",
        "statement_type": None,
        "errors": [],
        "warnings": [],
        "repair_hints": [],
        "tables": [],
        "columns": [],
        "joins": [],
        "aggregations": [],
        "group_by": [],
        "aliases": {"tables": {}, "ctes": [], "subqueries": [], "select": []},
        "checks": {
            "syntax": False,
            "parse": False,
            "statement_type": False,
            "tables": False,
            "columns": False,
            "joins": False,
            "aggregations": False,
            "group_by": False,
            "aliases": False,
        },
    }


def _finalize_report(report: Dict[str, Any]) -> Dict[str, Any]:
    report["valid"] = len(report["errors"]) == 0
    for check in report["checks"]:
        if check == "parse":
            report["checks"][check] = not any(err["check"] == "parse" for err in report["errors"])
        else:
            report["checks"][check] = not any(err["check"] == check for err in report["errors"])
    report["repair_hints"] = list(dict.fromkeys(report["repair_hints"]))
    return report


def _add_error(report: Dict[str, Any], check: str, message: str, hint: str) -> None:
    report["errors"].append({"check": check, "message": message, "hint": hint})
    report["repair_hints"].append(hint)


def _add_warning(report: Dict[str, Any], check: str, message: str, hint: Optional[str] = None) -> None:
    report["warnings"].append({"check": check, "message": message, "hint": hint})
    if hint:
        report["repair_hints"].append(hint)


def _build_schema_catalog(schema_metadata: dict) -> Dict[str, set]:
    catalog = {}
    for table in _iter_schema_tables(schema_metadata):
        table_name = _normalize_identifier(table.get("table_name"))
        if not table_name:
            continue
        catalog[table_name] = {
            _column_name(column)
            for column in table.get("columns", [])
            if _column_name(column)
        }
    return catalog


def _iter_schema_tables(schema_metadata: dict) -> List[dict]:
    tables = (schema_metadata or {}).get("tables")
    if isinstance(tables, list):
        return tables
    if isinstance(schema_metadata, dict) and all(isinstance(value, dict) for value in schema_metadata.values()):
        return [
            {"table_name": table_name, **table_meta}
            for table_name, table_meta in schema_metadata.items()
        ]
    return []


def _column_name(column: dict) -> str:
    return _normalize_identifier(
        column.get("clean_name") or column.get("name") or column.get("column_name")
    )


def _contains_disallowed_statement(root: exp.Expression) -> bool:
    disallowed = (exp.Delete, exp.Update, exp.Drop, exp.Alter)
    return isinstance(root, disallowed) or any(isinstance(node, disallowed) for node in root.walk())


def _cte_aliases(root: exp.Expression) -> set:
    return {_normalize_identifier(cte.alias) for cte in root.find_all(exp.CTE) if cte.alias}


def _subquery_aliases(root: exp.Expression) -> set:
    aliases = set()
    for subquery in root.find_all(exp.Subquery):
        alias = subquery.alias_or_name
        if alias:
            aliases.add(_normalize_identifier(alias))
    return aliases


def _table_aliases(
    root: exp.Expression,
    catalog: Dict[str, set],
    cte_aliases: set,
    subquery_aliases: set,
) -> Tuple[Dict[str, str], List[str]]:
    aliases: Dict[str, str] = {}
    errors: List[str] = []

    for table in root.find_all(exp.Table):
        table_name = _normalize_identifier(table.name)
        alias = _normalize_identifier(table.alias_or_name or table_name)

        if table_name in cte_aliases or table_name in subquery_aliases:
            cte_aliases.add(alias)
            continue
        if table_name in {"information_schema", "pg_catalog", "pg_class", "pg_attribute", "pg_type", "pg_tables"}:
            continue
        if table_name not in catalog:
            errors.append(f"Unknown table: {table_name}")
            continue

        aliases[alias] = table_name
        aliases.setdefault(table_name, table_name)

    return aliases, errors


def _validate_columns(
    root: exp.Expression,
    catalog: Dict[str, set],
    table_aliases: Dict[str, str],
    cte_aliases: set,
    subquery_aliases: set,
    report: Dict[str, Any],
) -> None:
    select_aliases = _select_aliases(root)
    seen = set()

    for column in root.find_all(exp.Column):
        table_qualifier = _normalize_identifier(column.table)
        column_name = _normalize_identifier(column.name)
        column_sql = column.sql(dialect="postgres")

        if column_name == "*":
            continue

        if (table_qualifier, column_name, column_sql) not in seen:
            report["columns"].append({"table": table_qualifier or None, "column": column_name, "sql": column_sql})
            seen.add((table_qualifier, column_name, column_sql))

        if table_qualifier:
            if table_qualifier in cte_aliases or table_qualifier in subquery_aliases:
                continue
            base_table = table_aliases.get(table_qualifier)
            if not base_table:
                _add_error(
                    report,
                    "aliases",
                    f"Unknown table alias used by column: {column_sql}",
                    "Use a table alias defined in FROM/JOIN or remove the invalid qualifier.",
                )
                continue
            if column_name not in catalog.get(base_table, set()):
                _add_error(
                    report,
                    "columns",
                    f"Unknown column: {base_table}.{column_name}",
                    f"Replace {column_sql} with a valid column from {base_table}.",
                )
            continue

        if column_name in select_aliases and _is_alias_reference_context(column):
            continue

        matching_tables = [table for table in set(table_aliases.values()) if column_name in catalog.get(table, set())]
        if len(matching_tables) == 1:
            continue
        if len(matching_tables) > 1:
            _add_error(
                report,
                "columns",
                f"Ambiguous unqualified column: {column_name}",
                f"Qualify {column_name} with the correct table name or alias.",
            )
        elif table_aliases and not cte_aliases and not subquery_aliases:
            _add_error(
                report,
                "columns",
                f"Unknown unqualified column: {column_name}",
                f"Use a valid column from one of: {', '.join(sorted(set(table_aliases.values())))}.",
            )


def _validate_joins(
    root: exp.Expression,
    catalog: Dict[str, set],
    table_aliases: Dict[str, str],
    cte_aliases: set,
    subquery_aliases: set,
    schema_metadata: dict,
    report: Dict[str, Any],
) -> None:
    relationship_pairs = _relationship_pairs(schema_metadata)

    for join in root.find_all(exp.Join):
        join_sql = join.sql(dialect="postgres")
        on_expr = join.args.get("on")
        using_expr = join.args.get("using")
        join_info = {"sql": join_sql, "has_condition": bool(on_expr or using_expr), "relationship_validated": None}
        report["joins"].append(join_info)

        if not on_expr and not using_expr:
            if _is_allowed_cross_join(join, cte_aliases, subquery_aliases):
                join_info["relationship_validated"] = False
                _add_warning(
                    report,
                    "joins",
                    f"CROSS JOIN allowed for CTE/subquery relation: {join_sql}",
                    "Use CROSS JOIN only for intentional one-row aggregate CTEs or scalar subqueries.",
                )
                continue
            _add_error(
                report,
                "joins",
                f"JOIN missing ON/USING predicate: {join_sql}",
                "Add an explicit ON/USING predicate backed by schema relationship metadata.",
            )
            continue

        if using_expr:
            _add_warning(
                report,
                "joins",
                f"USING join predicate found: {join_sql}",
                "Prefer explicit ON predicates so column/table ownership is unambiguous.",
            )

        if on_expr:
            eq_pairs = _join_equality_pairs(on_expr, table_aliases, cte_aliases, subquery_aliases)
            if not eq_pairs:
                _add_warning(
                    report,
                    "joins",
                    f"JOIN predicate has no simple equality pair: {join_sql}",
                    "Use equality joins between foreign-key and referenced-key columns where possible.",
                )
                continue

            if relationship_pairs:
                for left, right in eq_pairs:
                    if (left, right) not in relationship_pairs and (right, left) not in relationship_pairs:
                        _add_error(
                            report,
                            "joins",
                            f"Join predicate is not backed by relationship metadata: {left[0]}.{left[1]} = {right[0]}.{right[1]}",
                            "Use a join predicate that follows schema foreign-key metadata.",
                        )
                    else:
                        join_info["relationship_validated"] = True
            else:
                join_info["relationship_validated"] = False
                _add_warning(
                    report,
                    "joins",
                    "No relationship metadata available to validate join predicate.",
                    "Add schema relationship/foreign-key metadata for compiler-grade join validation.",
                )


def _validate_aggregations(root: exp.Expression, table_aliases: Dict[str, str], report: Dict[str, Any]) -> None:
    for select in root.find_all(exp.Select):
        aggregate_expressions = []
        for aggregate in select.find_all(exp.AggFunc):
            if _nearest_select(aggregate) is select and not _has_ancestor(aggregate, exp.Window):
                aggregate_expressions.append(aggregate.sql(dialect="postgres"))
                _validate_aggregate_function(aggregate, select, report)

        if not aggregate_expressions:
            continue

        report["aggregations"].extend(aggregate_expressions)

        group = select.args.get("group")
        group_sqls = {_canonical_sql(expr) for expr in group.expressions} if group else set()
        report["group_by"].extend(sorted(group_sqls))

        non_agg_selects = []
        for projection in select.expressions:
            expression = projection.this if isinstance(projection, exp.Alias) else projection
            if isinstance(expression, exp.Star):
                non_agg_selects.append(expression.sql(dialect="postgres"))
            elif _contains_non_window_aggregate(expression):
                continue
            elif isinstance(expression, exp.Literal):
                continue
            else:
                non_agg_selects.append(_canonical_sql(expression))

        if non_agg_selects and not group_sqls:
            _add_error(
                report,
                "group_by",
                "Aggregate query selects non-aggregated expressions without GROUP BY.",
                "Add GROUP BY for every non-aggregated SELECT expression or aggregate those expressions.",
            )
            continue

        for expr_sql in non_agg_selects:
            if expr_sql not in group_sqls:
                _add_error(
                    report,
                    "group_by",
                    f"Non-aggregated SELECT expression is missing from GROUP BY: {expr_sql}",
                    f"Add {expr_sql} to GROUP BY or wrap it in an aggregate function.",
                )


def _validate_aggregate_function(aggregate: exp.AggFunc, select: exp.Select, report: Dict[str, Any]) -> None:
    aggregate_sql = aggregate.sql(dialect="postgres")
    if not isinstance(aggregate, SUPPORTED_AGGREGATE_TYPES):
        _add_error(
            report,
            "aggregations",
            f"Unsupported aggregate function: {aggregate_sql}",
            "Use one of COUNT, SUM, AVG, MIN, or MAX for deterministic aggregate validation.",
        )

    if isinstance(aggregate.this, exp.Star) and not isinstance(aggregate, exp.Count):
        _add_error(
            report,
            "aggregations",
            f"Invalid aggregate argument: {aggregate_sql}",
            "Only COUNT may use *; use a concrete numeric column for SUM/AVG/MIN/MAX.",
        )

    for nested in aggregate.find_all(exp.AggFunc):
        if nested is aggregate:
            continue
        if _nearest_select(nested) is select:
            _add_error(
                report,
                "aggregations",
                f"Nested aggregate function is invalid in the same SELECT scope: {aggregate_sql}",
                "Compute nested aggregates in separate subqueries or CTEs.",
            )
            break


def _validate_aliases(
    root: exp.Expression,
    catalog: Dict[str, set],
    table_aliases: Dict[str, str],
    cte_aliases: set,
    subquery_aliases: set,
    report: Dict[str, Any],
) -> None:
    select_aliases = _select_aliases(root)
    report["aliases"]["select"] = sorted(select_aliases)

    duplicates = _duplicate_aliases(root)
    for alias in duplicates:
        _add_error(
            report,
            "aliases",
            f"Duplicate SELECT alias: {alias}",
            "Use unique output aliases for every selected expression.",
        )

    for order in root.find_all(exp.Ordered):
        order_expr = order.this
        if isinstance(order_expr, exp.Column) and not order_expr.table:
            name = _normalize_identifier(order_expr.name)
            if name in select_aliases:
                continue
            matching_tables = [table for table in set(table_aliases.values()) if name in catalog.get(table, set())]
            if not matching_tables:
                _add_error(
                    report,
                    "aliases",
                    f"ORDER BY references unknown alias or column: {name}",
                    "Order by a selected alias or a valid table column.",
                )


def _relationship_pairs(schema_metadata: dict) -> set:
    try:
        graph = RelationshipGraphManager(schema_metadata)
    except RelationshipPlanningError:
        return set()
    pairs = set()
    for edge in graph.edges:
        pairs.add(((edge.left_table, edge.left_column), (edge.right_table, edge.right_column)))
        pairs.add(((edge.right_table, edge.right_column), (edge.left_table, edge.left_column)))
    return pairs


def _is_allowed_cross_join(join: exp.Join, cte_aliases: set, subquery_aliases: set) -> bool:
    if (join.kind or "").upper() not in ("", "CROSS"):
        return False
    target = join.this
    if isinstance(target, exp.Table):
        target_name = _normalize_identifier(target.name)
        target_alias = _normalize_identifier(target.alias_or_name)
        return target_name in cte_aliases or target_alias in cte_aliases
    if isinstance(target, exp.Subquery):
        target_alias = _normalize_identifier(target.alias_or_name)
        return bool(target_alias and target_alias in subquery_aliases)
    return False


def _join_equality_pairs(
    on_expr: exp.Expression,
    table_aliases: Dict[str, str],
    cte_aliases: set,
    subquery_aliases: set,
) -> List[Tuple[Tuple[str, str], Tuple[str, str]]]:
    pairs = []
    for equality in on_expr.find_all(exp.EQ):
        left = equality.left
        right = equality.right
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue
        left_pair = _column_pair(left, table_aliases, cte_aliases, subquery_aliases)
        right_pair = _column_pair(right, table_aliases, cte_aliases, subquery_aliases)
        if left_pair and right_pair:
            pairs.append((left_pair, right_pair))
    return pairs


def _column_pair(
    column: exp.Column,
    table_aliases: Dict[str, str],
    cte_aliases: set,
    subquery_aliases: set,
) -> Optional[Tuple[str, str]]:
    qualifier = _normalize_identifier(column.table)
    column_name = _normalize_identifier(column.name)
    if not qualifier or qualifier in cte_aliases or qualifier in subquery_aliases:
        return None
    base_table = table_aliases.get(qualifier)
    if not base_table:
        return None
    return base_table, column_name


def _select_aliases(root: exp.Expression) -> set:
    return {
        _normalize_identifier(alias.alias)
        for alias in root.find_all(exp.Alias)
        if alias.alias
    }


def _duplicate_aliases(root: exp.Expression) -> set:
    seen = set()
    duplicates = set()
    for alias in root.find_all(exp.Alias):
        name = _normalize_identifier(alias.alias)
        if not name:
            continue
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    return duplicates


def _contains_non_window_aggregate(expression: exp.Expression) -> bool:
    return any(not _has_ancestor(aggregate, exp.Window) for aggregate in expression.find_all(exp.AggFunc))


def _has_ancestor(expression: exp.Expression, ancestor_type) -> bool:
    parent = expression.parent
    while parent is not None:
        if isinstance(parent, ancestor_type):
            return True
        parent = parent.parent
    return False


def _nearest_select(expression: exp.Expression) -> Optional[exp.Select]:
    parent = expression.parent
    while parent is not None:
        if isinstance(parent, exp.Select):
            return parent
        parent = parent.parent
    return None


def _is_alias_reference_context(column: exp.Column) -> bool:
    parent = column.parent
    return isinstance(parent, (exp.Ordered, exp.Group, exp.Having, exp.Qualify))


def _canonical_sql(expression: exp.Expression) -> str:
    return expression.sql(dialect="postgres", normalize=True).lower()


def _normalize_identifier(value: Any) -> str:
    return str(value or "").strip().lower()


def format_validation_repair_context(prefix: str, report: Dict[str, Any]) -> str:
    lines = [prefix]
    if report.get("errors"):
        lines.append("AST validation errors:")
        for error in report["errors"]:
            lines.append(f"- [{error['check']}] {error['message']}")
    if report.get("repair_hints"):
        lines.append("Repair hints:")
        for hint in report["repair_hints"]:
            lines.append(f"- {hint}")
    return "\n".join(lines)


def _sql_retry_key(sql: str) -> str:
    return re.sub(r"\s+", " ", (sql or "").strip().rstrip(";")).lower()


def _repair_confidence(repair_attempts: List[Dict[str, Any]]) -> float:
    confidences = [attempt.get("confidence", 0.0) for attempt in repair_attempts]
    return round(max(confidences), 4) if confidences else 0.0


def _add_timing(timings: Dict[str, int], key: str, elapsed_seconds: float) -> None:
    timings[key] = timings.get(key, 0) + int(elapsed_seconds * 1000)


def _with_validation_timings(response: dict, timings: Dict[str, int]) -> dict:
    response["timings"] = dict(timings)
    val_ms = (
        timings.get("syntax_validation_ms", 0) +
        timings.get("table_column_validation_ms", 0) +
        timings.get("ast_validation_ms", 0) +
        timings.get("explain_ms", 0) +
        timings.get("repair_ms", 0)
    )
    PipelineProfiler.add_ms_direct("validator_ms", val_ms)
    PipelineProfiler.add_ms_direct("db_ms", timings.get("execution_ms", 0))
    return response


def _syntax_repair_blocked(sql: str, syntax_msg: str) -> bool:
    sql_upper = (sql or "").strip().upper()
    msg = (syntax_msg or "").lower()
    return (
        sql_upper.startswith("OFF_TOPIC")
        or "dangerous keyword" in msg
        or any(keyword in sql_upper for keyword in ["DELETE ", "UPDATE ", "DROP ", "ALTER ", "TRUNCATE ", "INSERT ", "CREATE "])
    )


def _record_llm_repair(repair_attempts: List[Dict[str, Any]], sql: str, reason: str) -> None:
    repair_attempts.append({
        "sql": sql,
        "changed": True,
        "confidence": 0.35,
        "stages": [{"stage": "llm_repair_fallback", "confidence": 0.35, "description": reason}],
        "hints": [reason],
    })


def _repeated_repair_response(
    current_sql: str,
    healed: bool,
    warnings: List[str],
    ast_report: Optional[Dict[str, Any]],
    validation_reports: List[Dict[str, Any]],
    repair_attempts: List[Dict[str, Any]],
) -> dict:
    return {
        "success": False,
        "error": "Repair stopped because a retry produced a repeated SQL candidate.",
        "sql": current_sql,
        "healed": healed,
        "warnings": warnings,
        "validation_report": ast_report,
        "validation_reports": validation_reports,
        "repair_hints": ast_report.get("repair_hints", []) if ast_report else [],
        "repair_attempts": repair_attempts,
        "repair_confidence": _repair_confidence(repair_attempts),
    }


def explain_query(sql: str, conn_str: str) -> Tuple[bool, str, Optional[dict]]:
    """Run EXPLAIN (no actual execution) to validate query plan."""
    conn = None
    try:
        conn = pooled_connect(conn_str)
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
            plan = cur.fetchone()[0]
        return True, "Query plan generated successfully", plan
    except psycopg2.Error as e:
        return False, str(e), None
    finally:
        if conn is not None:
            conn.close()


def execute_query(sql: str, conn_str: str, timeout_ms: int = 30000) -> dict:
    """Execute the validated query and return results."""
    start = time.time()
    conn = None
    try:
        conn = pooled_connect(conn_str)
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {timeout_ms}")
            cur.execute(sql)
            rows = cur.fetchmany(500)  # cap at 500 rows for UI display
            columns = [desc[0] for desc in cur.description] if cur.description else []
        elapsed = int((time.time() - start) * 1000)
        return {
            "success": True,
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "execution_time_ms": elapsed,
            "error": None
        }
    except psycopg2.Error as e:
        return {
            "success": False,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": int((time.time() - start) * 1000),
            "error": str(e)
        }
    finally:
        if conn is not None:
            conn.close()


def validate_and_execute(
    sql: str,
    schema_metadata: dict,
    conn_str: str,
    llm_heal_fn=None,
    schema_ddl: str = "",
    max_retries: int = 3,
    question: Optional[str] = None
) -> dict:
    """Full validation + self-healing execution pipeline."""
    current_sql = sql
    healed = False
    validation_reports = []
    col_warnings = []
    ast_report = None
    repair_attempts = []
    seen_sql = {_sql_retry_key(current_sql)}
    timings = {
        "syntax_validation_ms": 0,
        "table_column_validation_ms": 0,
        "ast_validation_ms": 0,
        "explain_ms": 0,
        "repair_ms": 0,
        "execution_ms": 0,
    }

    for attempt in range(max_retries):
        # Step 1: legacy syntax pre-check
        with observed_stage("validation.syntax", attempt=attempt) as timer:
            syntax_ok, syntax_msg = validate_sql_syntax(current_sql)
        _add_timing(timings, "syntax_validation_ms", timer.elapsed_seconds)
        if not syntax_ok:
            if not _syntax_repair_blocked(current_sql, syntax_msg):
                with observed_stage("repair.deterministic.syntax", attempt=attempt) as repair_timer:
                    deterministic_repair = repair_sql_deterministically(current_sql, None, schema_metadata)
                _add_timing(timings, "repair_ms", repair_timer.elapsed_seconds)
                observe_repair(
                    repair_timer.elapsed_seconds,
                    "deterministic",
                    "changed" if deterministic_repair.changed else "unchanged",
                )
                if deterministic_repair.changed:
                    repaired_key = _sql_retry_key(deterministic_repair.sql)
                    if repaired_key in seen_sql:
                        return _with_validation_timings(_repeated_repair_response(
                            current_sql,
                            healed,
                            col_warnings,
                            ast_report,
                            validation_reports,
                            repair_attempts,
                        ), timings)
                    seen_sql.add(repaired_key)
                    repair_attempts.append(deterministic_repair.as_dict())
                    current_sql = deterministic_repair.sql
                    healed = True
                    continue

            if llm_heal_fn and attempt < max_retries - 1 and not _syntax_repair_blocked(current_sql, syntax_msg):
                with observed_stage("repair.llm.syntax", attempt=attempt) as repair_timer:
                    repaired_sql = llm_heal_fn(current_sql, syntax_msg, schema_ddl)
                _add_timing(timings, "repair_ms", repair_timer.elapsed_seconds)
                observe_repair(repair_timer.elapsed_seconds, "llm", "changed")
                repaired_key = _sql_retry_key(repaired_sql)
                if repaired_key in seen_sql:
                    return _with_validation_timings(_repeated_repair_response(
                        current_sql,
                        healed,
                        col_warnings,
                        ast_report,
                        validation_reports,
                        repair_attempts,
                    ), timings)
                seen_sql.add(repaired_key)
                _record_llm_repair(repair_attempts, repaired_sql, "LLM repair fallback used for syntax validation failure.")
                current_sql = repaired_sql
                healed = True
                continue

            return _with_validation_timings({
                "success": False,
                "error": syntax_msg,
                "sql": current_sql,
                "healed": healed,
                "validation_reports": validation_reports,
                "repair_hints": [],
                "repair_attempts": repair_attempts,
                "repair_confidence": _repair_confidence(repair_attempts)
            }, timings)

        # Step 2: legacy table scan check, preserved for compatibility
        with observed_stage("validation.table_columns", attempt=attempt) as timer:
            cols_ok, col_warnings = validate_table_columns(current_sql, schema_metadata)
        _add_timing(timings, "table_column_validation_ms", timer.elapsed_seconds)

        # Step 3: compiler-grade AST validation
        with observed_stage("validation.ast", attempt=attempt) as timer:
            ast_report = validate_sql_ast(current_sql, schema_metadata, question=question)
        _add_timing(timings, "ast_validation_ms", timer.elapsed_seconds)
        validation_reports.append(ast_report)
        combined_warnings = col_warnings + [w["message"] for w in ast_report.get("warnings", [])]

        if not ast_report.get("valid", False):
            ast_error_context = format_validation_repair_context("AST validation failed.", ast_report)
            with observed_stage("repair.deterministic.ast", attempt=attempt) as repair_timer:
                deterministic_repair = repair_sql_deterministically(current_sql, ast_report, schema_metadata)
            _add_timing(timings, "repair_ms", repair_timer.elapsed_seconds)
            observe_repair(
                repair_timer.elapsed_seconds,
                "deterministic",
                "changed" if deterministic_repair.changed else "unchanged",
            )
            if deterministic_repair.changed:
                repaired_key = _sql_retry_key(deterministic_repair.sql)
                if repaired_key in seen_sql:
                    return _with_validation_timings(_repeated_repair_response(
                        current_sql,
                        healed,
                        combined_warnings,
                        ast_report,
                        validation_reports,
                        repair_attempts,
                    ), timings)
                seen_sql.add(repaired_key)
                repair_attempts.append(deterministic_repair.as_dict())
                current_sql = deterministic_repair.sql
                healed = True
                continue

            if llm_heal_fn and attempt < max_retries - 1:
                with observed_stage("repair.llm.ast", attempt=attempt) as repair_timer:
                    repaired_sql = llm_heal_fn(current_sql, ast_error_context, schema_ddl)
                _add_timing(timings, "repair_ms", repair_timer.elapsed_seconds)
                observe_repair(repair_timer.elapsed_seconds, "llm", "changed")
                repaired_key = _sql_retry_key(repaired_sql)
                if repaired_key in seen_sql:
                    return _with_validation_timings(_repeated_repair_response(
                        current_sql,
                        healed,
                        combined_warnings,
                        ast_report,
                        validation_reports,
                        repair_attempts,
                    ), timings)
                seen_sql.add(repaired_key)
                _record_llm_repair(repair_attempts, repaired_sql, "LLM repair fallback used for AST validation failure.")
                current_sql = repaired_sql
                healed = True
                continue
            return _with_validation_timings({
                "success": False,
                "error": ast_error_context,
                "sql": current_sql,
                "healed": healed,
                "warnings": combined_warnings,
                "validation_report": ast_report,
                "validation_reports": validation_reports,
                "repair_hints": ast_report.get("repair_hints", []),
                "repair_attempts": repair_attempts,
                "repair_confidence": _repair_confidence(repair_attempts)
            }, timings)

        # Step 4: EXPLAIN plan check
        with observed_stage("validation.explain", attempt=attempt) as timer:
            plan_ok, plan_msg, plan = explain_query(current_sql, conn_str)
        _add_timing(timings, "explain_ms", timer.elapsed_seconds)
        if plan_ok:
            break
        if llm_heal_fn and attempt < max_retries - 1:
            repair_context = format_validation_repair_context(
                f"PostgreSQL EXPLAIN failed: {plan_msg}",
                ast_report
            )
            with observed_stage("repair.llm.explain", attempt=attempt) as repair_timer:
                repaired_sql = llm_heal_fn(current_sql, repair_context, schema_ddl)
            _add_timing(timings, "repair_ms", repair_timer.elapsed_seconds)
            observe_repair(repair_timer.elapsed_seconds, "llm", "changed")
            repaired_key = _sql_retry_key(repaired_sql)
            if repaired_key in seen_sql:
                return _with_validation_timings(_repeated_repair_response(
                    current_sql,
                    healed,
                    combined_warnings,
                    ast_report,
                    validation_reports,
                    repair_attempts,
                ), timings)
            seen_sql.add(repaired_key)
            _record_llm_repair(repair_attempts, repaired_sql, "LLM repair fallback used for PostgreSQL EXPLAIN failure.")
            current_sql = repaired_sql
            healed = True
        else:
            return _with_validation_timings({
                "success": False,
                "error": f"Query validation failed after {attempt+1} attempts: {plan_msg}",
                "sql": current_sql,
                "healed": healed,
                "warnings": combined_warnings,
                "validation_report": ast_report,
                "validation_reports": validation_reports,
                "repair_hints": ast_report.get("repair_hints", []) if ast_report else [],
                "repair_attempts": repair_attempts,
                "repair_confidence": _repair_confidence(repair_attempts)
            }, timings)
    else:
        return _with_validation_timings({
            "success": False,
            "error": "Query validation failed before execution.",
            "sql": current_sql,
            "healed": healed,
            "warnings": col_warnings,
            "validation_report": ast_report,
            "validation_reports": validation_reports,
            "repair_hints": ast_report.get("repair_hints", []) if ast_report else [],
            "repair_attempts": repair_attempts,
            "repair_confidence": _repair_confidence(repair_attempts)
        }, timings)
    
    # Step 5: Execute
    with observed_stage("sql.execute") as timer:
        result = execute_query(current_sql, conn_str)
    _add_timing(timings, "execution_ms", timer.elapsed_seconds)
    result["sql"] = current_sql
    result["healed"] = healed
    result["warnings"] = col_warnings + [w["message"] for w in ast_report.get("warnings", [])] if ast_report else col_warnings
    result["validation_report"] = ast_report
    result["validation_reports"] = validation_reports
    result["repair_hints"] = ast_report.get("repair_hints", []) if ast_report else []
    result["repair_attempts"] = repair_attempts
    result["repair_confidence"] = _repair_confidence(repair_attempts)
    result["deterministic_repaired"] = any(
        stage.get("stage") != "llm_repair_fallback"
        for attempt in repair_attempts
        for stage in attempt.get("stages", [])
    )
    result["plan"] = plan
    result["timings"] = timings
    log_event(
        "validation.finish",
        success=result.get("success", False),
        healed=healed,
        repair_ms=timings.get("repair_ms", 0),
        ast_validation_ms=timings.get("ast_validation_ms", 0),
        explain_ms=timings.get("explain_ms", 0),
        execution_ms=timings.get("execution_ms", 0),
    )
    return _with_validation_timings(result, timings)


async def validate_and_execute_async(
    sql: str,
    schema_metadata: dict,
    conn_str: str,
    llm_heal_fn=None,
    schema_ddl: str = "",
    max_retries: int = 3,
    question: Optional[str] = None
) -> dict:
    return await asyncio.to_thread(
        validate_and_execute,
        sql,
        schema_metadata,
        conn_str,
        llm_heal_fn,
        schema_ddl,
        max_retries,
        question,
    )
