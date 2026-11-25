"""
NLPSQL SYSTEM - PHASE 04B
Deterministic SQL Repair Stages

Applies conservative, AST-guided repairs before the LLM self-healing fallback.
Every repair is schema-bounded and returns confidence metadata.
"""

from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any, Dict, List, Optional, Sequence, Tuple
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from core.phase3_relationship_graph import RelationshipGraphManager, RelationshipPlanningError


MIN_COLUMN_MATCH = 0.82
MIN_ALIAS_MATCH = 0.78


@dataclass
class RepairResult:
    sql: str
    changed: bool
    confidence: float
    stages: List[Dict[str, Any]]
    hints: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sql": self.sql,
            "changed": self.changed,
            "confidence": round(self.confidence, 4),
            "stages": self.stages,
            "hints": self.hints,
        }


def repair_sql_deterministically(
    sql: str,
    validation_report: Optional[Dict[str, Any]],
    schema_metadata: dict,
) -> RepairResult:
    stages: List[Dict[str, Any]] = []
    hints: List[str] = []
    current_sql = sql

    syntax_sql, syntax_stage = repair_syntax_inconsistencies(current_sql)
    if syntax_stage:
        current_sql = syntax_sql
        stages.append(syntax_stage)
        hints.append(syntax_stage["description"])

    try:
        root = sqlglot.parse_one(current_sql, read="postgres")
    except ParseError:
        return _result(sql, current_sql, stages, hints)

    catalog = _schema_catalog(schema_metadata)
    try:
        graph = RelationshipGraphManager(schema_metadata)
    except RelationshipPlanningError:
        graph = None

    for repair_fn in [
        lambda node: repair_invalid_joins(node, graph),
        lambda node: repair_invalid_columns(node, catalog),
        repair_aggregation_misuse,
        lambda node: repair_alias_conflicts(node, catalog),
    ]:
        stage_result = repair_fn(root)
        if stage_result:
            stages.extend(stage_result)
            hints.extend(stage["description"] for stage in stage_result)

    repaired_sql = _ensure_semicolon(root.sql(dialect="postgres"))
    return _result(sql, repaired_sql, stages, hints)


def repair_syntax_inconsistencies(sql: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    if _contains_dangerous_keyword(sql):
        return sql, None

    repaired = sql.strip()
    repaired = _strip_markdown_fence(repaired)
    repaired = re.sub(r",\s+(FROM|WHERE|GROUP\s+BY|ORDER\s+BY|LIMIT)\b", r" \1", repaired, flags=re.IGNORECASE)
    repaired = re.sub(r"\s+", " ", repaired).strip()

    if repaired != sql.strip():
        return repaired, {
            "stage": "syntax_inconsistencies",
            "confidence": 0.74,
            "description": "Normalized markdown/preamble syntax and removed dangling SQL commas.",
        }
    return sql, None


def repair_invalid_joins(root: exp.Expression, graph: Optional[RelationshipGraphManager]) -> List[Dict[str, Any]]:
    stages: List[Dict[str, Any]] = []
    for select in root.find_all(exp.Select):
        from_table = _from_table(select)
        if not from_table:
            continue

        joined_aliases: Dict[str, str] = {}
        base_table, base_alias = _table_identity(from_table)
        if base_table:
            joined_aliases[base_table] = base_alias

        repaired_joins = []
        changed_joins = False
        for join in select.args.get("joins") or []:
            target_table_expr = join.this if isinstance(join.this, exp.Table) else None
            target_table, target_alias = _table_identity(target_table_expr)
            if not target_table:
                repaired_joins.append(join)
                continue

            on_expr = join.args.get("on")
            using_expr = join.args.get("using")
            replacement_on = _relationship_join_condition(target_table, target_alias, joined_aliases, graph)

            if replacement_on and (not on_expr and not using_expr or _join_condition_not_relationship_backed(on_expr, graph)):
                join.set("on", replacement_on)
                join.set("using", None)
                stages.append({
                    "stage": "invalid_joins",
                    "confidence": 0.9,
                    "description": f"Repaired join predicate for {target_table} using schema relationship metadata.",
                })
            elif not on_expr and not using_expr and _join_target_unused(select, target_alias):
                changed_joins = True
                stages.append({
                    "stage": "invalid_joins",
                    "confidence": 0.62,
                    "description": f"Removed unused cartesian join target {target_table}.",
                })
                continue

            repaired_joins.append(join)
            joined_aliases.setdefault(target_table, target_alias)

        if changed_joins:
            select.set("joins", repaired_joins)

    return stages


def repair_invalid_columns(root: exp.Expression, catalog: Dict[str, set]) -> List[Dict[str, Any]]:
    stages: List[Dict[str, Any]] = []
    table_aliases = _table_aliases(root, catalog)

    for column in root.find_all(exp.Column):
        table_qualifier = _normalize(column.table)
        column_name = _normalize(column.name)
        if not column_name or column_name == "*":
            continue

        if table_qualifier:
            base_table = table_aliases.get(table_qualifier)
            if not base_table:
                continue
            if column_name in catalog.get(base_table, set()):
                continue
            replacement = _closest(column_name, catalog.get(base_table, set()), MIN_COLUMN_MATCH)
            if replacement:
                column.set("this", exp.to_identifier(replacement))
                stages.append({
                    "stage": "invalid_columns",
                    "confidence": 0.86,
                    "description": f"Replaced unknown column {base_table}.{column_name} with {base_table}.{replacement}.",
                })
            continue

        candidate = _unique_close_column(column_name, set(table_aliases.values()), catalog)
        if candidate:
            base_table, replacement = candidate
            column.set("this", exp.to_identifier(replacement))
            stages.append({
                "stage": "invalid_columns",
                "confidence": 0.8,
                "description": f"Replaced unknown column {column_name} with {base_table}.{replacement}.",
            })

    return stages


def repair_aggregation_misuse(root: exp.Expression) -> List[Dict[str, Any]]:
    stages: List[Dict[str, Any]] = []
    for select in root.find_all(exp.Select):
        if not _select_has_local_aggregate(select):
            continue

        non_agg_expressions = []
        for projection in select.expressions:
            expression = projection.this if isinstance(projection, exp.Alias) else projection
            if isinstance(expression, exp.Star):
                continue
            if _contains_local_aggregate(expression, select):
                continue
            if isinstance(expression, exp.Literal):
                continue
            non_agg_expressions.append(expression)

        if not non_agg_expressions:
            continue

        group = select.args.get("group")
        existing = {_canonical(expr) for expr in group.expressions} if group else set()
        missing = [expr for expr in non_agg_expressions if _canonical(expr) not in existing]
        if missing:
            select.group_by(*(expr.copy() for expr in missing), copy=False)
            stages.append({
                "stage": "aggregation_misuse",
                "confidence": 0.88,
                "description": "Added missing GROUP BY expressions for non-aggregated SELECT columns.",
            })

    return stages


def repair_alias_conflicts(root: exp.Expression, catalog: Dict[str, set]) -> List[Dict[str, Any]]:
    stages: List[Dict[str, Any]] = []
    select_aliases: List[str] = []
    alias_counts: Dict[str, int] = {}

    for alias in root.find_all(exp.Alias):
        alias_name = _normalize(alias.alias)
        if not alias_name:
            continue
        alias_counts[alias_name] = alias_counts.get(alias_name, 0) + 1
        if alias_counts[alias_name] > 1:
            new_alias = f"{alias_name}_{alias_counts[alias_name]}"
            alias.set("alias", exp.to_identifier(new_alias))
            select_aliases.append(new_alias)
            stages.append({
                "stage": "alias_conflicts",
                "confidence": 0.82,
                "description": f"Renamed duplicate SELECT alias {alias_name} to {new_alias}.",
            })
        else:
            select_aliases.append(alias_name)

    final_aliases = set(select_aliases)
    table_aliases = _table_aliases(root, catalog)
    known_columns = set()
    for table in set(table_aliases.values()):
        known_columns.update(catalog.get(table, set()))

    for order in root.find_all(exp.Ordered):
        order_expr = order.this
        if not isinstance(order_expr, exp.Column) or order_expr.table:
            continue
        order_name = _normalize(order_expr.name)
        if order_name in final_aliases or order_name in known_columns:
            continue
        replacement = _closest(order_name, final_aliases, MIN_ALIAS_MATCH)
        if replacement:
            order_expr.set("this", exp.to_identifier(replacement))
            stages.append({
                "stage": "alias_conflicts",
                "confidence": 0.76,
                "description": f"Replaced ORDER BY reference {order_name} with alias {replacement}.",
            })

    return stages


def _result(original_sql: str, repaired_sql: str, stages: List[Dict[str, Any]], hints: List[str]) -> RepairResult:
    changed = _sql_key(original_sql) != _sql_key(repaired_sql)
    confidence = min([stage["confidence"] for stage in stages], default=0.0)
    return RepairResult(
        sql=repaired_sql if changed else original_sql,
        changed=changed,
        confidence=round(confidence, 4),
        stages=stages,
        hints=list(dict.fromkeys(hints)),
    )


def _schema_catalog(schema_metadata: dict) -> Dict[str, set]:
    catalog = {}
    for table in schema_metadata.get("tables", []):
        table_name = _normalize(table.get("table_name"))
        if not table_name:
            continue
        catalog[table_name] = {
            _normalize(column.get("clean_name"))
            for column in table.get("columns", [])
            if column.get("clean_name")
        }
    return catalog


def _table_aliases(root: exp.Expression, catalog: Dict[str, set]) -> Dict[str, str]:
    aliases = {}
    ctes = {_normalize(cte.alias) for cte in root.find_all(exp.CTE) if cte.alias}
    for table in root.find_all(exp.Table):
        table_name = _normalize(table.name)
        alias = _normalize(table.alias_or_name or table_name)
        if table_name in ctes or table_name not in catalog:
            continue
        aliases[alias] = table_name
        aliases.setdefault(table_name, table_name)
    return aliases


def _from_table(select: exp.Select) -> Optional[exp.Table]:
    from_expr = select.args.get("from_")
    if from_expr and isinstance(from_expr.this, exp.Table):
        return from_expr.this
    return None


def _table_identity(table: Optional[exp.Table]) -> Tuple[Optional[str], Optional[str]]:
    if not table:
        return None, None
    table_name = _normalize(table.name)
    alias = _normalize(table.alias_or_name or table_name)
    return table_name, alias


def _relationship_join_condition(
    target_table: str,
    target_alias: str,
    joined_aliases: Dict[str, str],
    graph: Optional[RelationshipGraphManager],
) -> Optional[exp.Expression]:
    if not graph:
        return None

    candidates = []
    for edge in graph.edges:
        if edge.left_table == target_table and edge.right_table in joined_aliases:
            candidates.append((edge.confidence, edge.right_table, edge.right_column, target_alias, edge.left_column))
        elif edge.right_table == target_table and edge.left_table in joined_aliases:
            candidates.append((edge.confidence, edge.left_table, edge.left_column, target_alias, edge.right_column))

    if not candidates:
        return None

    _, left_table, left_column, right_alias, right_column = sorted(candidates, reverse=True)[0]
    left_alias = joined_aliases[left_table]
    return exp.EQ(
        this=exp.column(left_column, table=left_alias),
        expression=exp.column(right_column, table=right_alias),
    )


def _join_condition_not_relationship_backed(
    on_expr: Optional[exp.Expression],
    graph: Optional[RelationshipGraphManager],
) -> bool:
    if not on_expr or not graph or not graph.edges:
        return False
    relationship_pairs = {
        ((edge.left_table, edge.left_column), (edge.right_table, edge.right_column))
        for edge in graph.edges
    }
    relationship_pairs.update({
        ((edge.right_table, edge.right_column), (edge.left_table, edge.left_column))
        for edge in graph.edges
    })
    for equality in on_expr.find_all(exp.EQ):
        left = equality.left
        right = equality.right
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue
        pair = ((_normalize(left.table), _normalize(left.name)), (_normalize(right.table), _normalize(right.name)))
        if pair not in relationship_pairs:
            return True
    return False


def _join_target_unused(select: exp.Select, target_alias: str) -> bool:
    if any(isinstance(expression, exp.Star) for expression in select.find_all(exp.Star)):
        return False
    for column in select.find_all(exp.Column):
        if _normalize(column.table) == target_alias:
            return False
    return True


def _unique_close_column(column_name: str, tables: set, catalog: Dict[str, set]) -> Optional[Tuple[str, str]]:
    matches = []
    for table in tables:
        replacement = _closest(column_name, catalog.get(table, set()), MIN_COLUMN_MATCH)
        if replacement:
            matches.append((table, replacement))
    return matches[0] if len(matches) == 1 else None


def _closest(value: str, candidates: Sequence[str], cutoff: float) -> Optional[str]:
    matches = get_close_matches(value, list(candidates), n=1, cutoff=cutoff)
    return matches[0] if matches else None


def _select_has_local_aggregate(select: exp.Select) -> bool:
    return any(_nearest_select(aggregate) is select for aggregate in select.find_all(exp.AggFunc))


def _contains_local_aggregate(expression: exp.Expression, select: exp.Select) -> bool:
    return any(_nearest_select(aggregate) is select for aggregate in expression.find_all(exp.AggFunc))


def _nearest_select(expression: exp.Expression) -> Optional[exp.Select]:
    parent = expression.parent
    while parent is not None:
        if isinstance(parent, exp.Select):
            return parent
        parent = parent.parent
    return None


def _canonical(expression: exp.Expression) -> str:
    return expression.sql(dialect="postgres", normalize=True).lower()


def _strip_markdown_fence(sql: str) -> str:
    if "```" not in sql:
        return sql
    match = re.search(r"```(?:sql)?\s*(.*?)\s*```", sql, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else sql.replace("```sql", "").replace("```", "").strip()


def _contains_dangerous_keyword(sql: str) -> bool:
    upper = sql.upper()
    return any(keyword in upper for keyword in ["DELETE ", "UPDATE ", "DROP ", "ALTER ", "TRUNCATE ", "INSERT ", "CREATE "])


def _ensure_semicolon(sql: str) -> str:
    return sql.rstrip(";") + ";"


def _sql_key(sql: str) -> str:
    return re.sub(r"\s+", " ", (sql or "").strip().rstrip(";")).lower()


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()
