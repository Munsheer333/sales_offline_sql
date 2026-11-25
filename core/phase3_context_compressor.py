"""
NLPSQL SYSTEM - PHASE 03D
Context Compression for SQL Generation

This module sits after retrieval and before prompt construction. It keeps the
existing schema_context contract intact while pruning schema DDL and retrieval
fragments down to the tables, columns, joins, and business metrics most likely
to matter for the user question.
"""

import asyncio
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.phase2_hybrid_retriever import expand_business_synonyms
from core.phase3_relationship_graph import (
    JoinPathPlanner,
    RelationshipGraphManager,
    RelationshipPlanningError,
)


DEFAULT_SCHEMA_TOKEN_BUDGET = 2200
DEFAULT_MAX_TABLES = 4
DEFAULT_MAX_COLUMNS_PER_TABLE = 10
MIN_COLUMNS_PER_TABLE = 4
COMPANY_NAMES = (
    "hpcl", "iocl", "bpcl", "shell", "gail", "cpcl", "hmel", "mrpl",
    "nel", "nrl", "ril", "reliance", "nayara", "ongc", "sma", "oil",
)

BUSINESS_RULE_COLUMNS = {
    "industry_performance": ["category"],
    "mom_level": ["sbu_name"],
}

METRIC_TABLE_PREFERENCES = {
    "actual_sales": ["mom_level", "industry_performance", "m60_level"],
    "target_quantity": ["m60_level"],
    "market_share_percentage": ["industry_performance"],
}

DIMENSION_CANDIDATES = {
    "company": ["comname", "coname", "company_name"],
    "companies": ["comname", "coname", "company_name"],
    "state": ["statename", "statecode"],
    "states": ["statename", "statecode"],
    "district": ["distname", "dist"],
    "districts": ["distname", "dist"],
    "sbu": ["sbu_name"],
    "strategic business unit": ["sbu_name"],
    "retail": ["sbu_name"],
    "i&c": ["sbu_name"],
    "lubes": ["sbu_name"],
    "product": ["productname", "materialgroupname", "prod1", "productname_org"],
    "products": ["productname", "materialgroupname", "prod1", "productname_org"],
    "zone": ["zone_name", "zone"],
    "region": ["region_name", "ro", "region"],
    "month": ["month_name", "yearmonth", "month_year"],
    "months": ["month_name", "yearmonth", "month_year"],
    "date": ["day_id", "yearmonth", "month_name"],
    "day": ["day_id"],
    "fiscal year": ["fiscal_year", "fiscalyear", "cur_fiscal_year", "curfiscalyear"],
    "fy": ["fiscal_year", "fiscalyear", "cur_fiscal_year", "curfiscalyear"],
    "current fiscal year": ["fiscal_year", "fiscalyear", "cur_fiscal_year", "curfiscalyear"],
    "current fy": ["fiscal_year", "fiscalyear", "cur_fiscal_year", "curfiscalyear"],
}


def compress_generation_context(
    question: str,
    schema_metadata: Optional[dict],
    schema_context: Optional[Sequence[dict]],
    token_budget: int = DEFAULT_SCHEMA_TOKEN_BUDGET,
    max_tables: int = DEFAULT_MAX_TABLES,
    max_columns_per_table: int = DEFAULT_MAX_COLUMNS_PER_TABLE,
) -> Dict[str, Any]:
    """
    Return prompt-compatible compressed schema context.

    The output deliberately keeps the old prompt inputs:
    - schema_ddl: string passed as full_schema_ddl
    - schema_context: List[dict] where every item has a description key
    """
    schema_index = _build_schema_index(schema_metadata)
    raw_context = list(schema_context or [])

    if not schema_index:
        return {
            "schema_ddl": "",
            "schema_context": _safe_schema_context(raw_context),
            "tables": [],
            "columns_by_table": {},
            "joins": [],
            "business_metrics": [],
            "token_estimate": 0,
            "raw_token_estimate": _estimate_tokens_from_context("", raw_context),
            "token_budget": token_budget,
            "compression_ratio": 1.0,
            "budget_exceeded": False,
            "relevance": {"tables": [], "columns": {}},
        }

    q_norm = _normalize(question)
    expanded_question = expand_business_synonyms(question)
    query_tokens = _tokens(expanded_question)
    retrieval_scores = _score_retrieval_context(raw_context, schema_index)
    table_relevance = _score_tables(q_norm, query_tokens, schema_index, retrieval_scores)
    selected_tables = _select_tables(
        table_relevance,
        max_tables=max_tables,
        intent_tables=_intent_tables(q_norm, schema_index),
    )
    if _has_named_company_signal(q_norm) and "industry_performance" in schema_index:
        selected_tables = ["industry_performance"]

    business_metrics = _extract_business_metrics(q_norm, selected_tables, schema_index)
    required_columns = _required_columns_for_question(q_norm, selected_tables, schema_index, business_metrics)

    join_plan = _plan_relevant_joins(selected_tables, schema_metadata)
    selected_tables = _include_join_path_tables(selected_tables, join_plan.get("joins", []), schema_index)
    _add_join_columns(required_columns, join_plan.get("joins", []), schema_index)

    current_limit = max(max_columns_per_table, MIN_COLUMNS_PER_TABLE)
    best_result: Optional[Dict[str, Any]] = None

    while current_limit >= MIN_COLUMNS_PER_TABLE:
        column_relevance = _score_columns(
            q_norm=q_norm,
            query_tokens=query_tokens,
            schema_index=schema_index,
            selected_tables=selected_tables,
            retrieval_scores=retrieval_scores,
            required_columns=required_columns,
        )
        columns_by_table = _select_columns_by_table(
            selected_tables=selected_tables,
            column_relevance=column_relevance,
            required_columns=required_columns,
            max_columns_per_table=current_limit,
        )

        schema_ddl = build_compressed_ddl(
            schema_index=schema_index,
            tables=selected_tables,
            columns_by_table=columns_by_table,
            joins=join_plan.get("joins", []),
            business_metrics=business_metrics,
        )
        compressed_context = build_compressed_schema_context(
            selected_tables=selected_tables,
            columns_by_table=columns_by_table,
            column_relevance=column_relevance,
            table_relevance=table_relevance,
            joins=join_plan.get("joins", []),
            business_metrics=business_metrics,
            schema_index=schema_index,
        )
        token_estimate = _estimate_tokens_from_context(schema_ddl, compressed_context)
        best_result = {
            "schema_ddl": schema_ddl,
            "schema_context": compressed_context,
            "tables": selected_tables,
            "columns_by_table": columns_by_table,
            "joins": join_plan.get("joins", []),
            "business_metrics": business_metrics,
            "token_estimate": token_estimate,
            "relevance": {
                "tables": _public_table_relevance(table_relevance, selected_tables),
                "columns": _public_column_relevance(column_relevance, selected_tables, columns_by_table),
            },
        }

        if token_estimate <= token_budget:
            break
        current_limit -= 2

    result = best_result or {
        "schema_ddl": "",
        "schema_context": [],
        "tables": selected_tables,
        "columns_by_table": {},
        "joins": [],
        "business_metrics": business_metrics,
        "token_estimate": 0,
        "relevance": {"tables": [], "columns": {}},
    }

    if result["token_estimate"] > token_budget:
        result = _trim_to_budget(result, token_budget)

    raw_token_estimate = estimate_uncompressed_context_tokens(schema_metadata, raw_context)
    result["raw_token_estimate"] = raw_token_estimate
    result["token_budget"] = token_budget
    result["compression_ratio"] = round(
        result["token_estimate"] / raw_token_estimate,
        4,
    ) if raw_token_estimate else 1.0
    result["budget_exceeded"] = result["token_estimate"] > token_budget
    return result


async def compress_generation_context_async(
    question: str,
    schema_metadata: Optional[dict],
    schema_context: Optional[Sequence[dict]],
    token_budget: int = DEFAULT_SCHEMA_TOKEN_BUDGET,
    max_tables: int = DEFAULT_MAX_TABLES,
    max_columns_per_table: int = DEFAULT_MAX_COLUMNS_PER_TABLE,
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        compress_generation_context,
        question,
        schema_metadata,
        schema_context,
        token_budget,
        max_tables,
        max_columns_per_table,
    )


def estimate_tokens(text: str) -> int:
    """Cheap prompt token estimate suitable for budget decisions."""
    if not text:
        return 0
    return max(1, int(math.ceil(len(text) / 4.0)))


def estimate_uncompressed_context_tokens(
    schema_metadata: Optional[dict],
    schema_context: Optional[Sequence[dict]],
) -> int:
    schema_index = _build_schema_index(schema_metadata)
    context = list(schema_context or [])
    retrieved_tables = _dedupe(item.get("table") for item in context if item.get("table"))
    tables = retrieved_tables or list(schema_index.keys())
    ddl = _build_uncompressed_ddl(schema_index, tables)
    return _estimate_tokens_from_context(ddl, context)


def build_compressed_ddl(
    schema_index: Dict[str, Dict[str, Any]],
    tables: Sequence[str],
    columns_by_table: Dict[str, List[str]],
    joins: Sequence[dict],
    business_metrics: Sequence[dict],
) -> str:
    lines: List[str] = ["-- Compressed schema context for SQL generation."]

    for table_name in tables:
        table = schema_index.get(table_name)
        if not table:
            continue
        columns = [col for col in columns_by_table.get(table_name, []) if col in table["columns"]]
        if not columns:
            continue

        lines.append(f"CREATE TABLE {table_name} (")
        for index, column_name in enumerate(columns):
            column = table["columns"][column_name]
            comma = "," if index < len(columns) - 1 else ""
            lines.append(f"    {column_name} {_column_type(column)}{comma}")
        lines.append(");")

    if business_metrics:
        lines.append("-- Relevant business metrics:")
        for metric in business_metrics:
            lines.append(
                "-- "
                + f"{metric['name']}: {metric['expression']} "
                + f"(confidence {metric['confidence']:.2f})"
            )

    if joins:
        lines.append("-- Relevant joins from schema foreign-key metadata:")
        for join in joins:
            lines.append(
                "-- "
                + f"{join['left_table']}.{join['left_column']} = "
                + f"{join['right_table']}.{join['right_column']} "
                + f"(confidence {float(join.get('confidence', 0.0)):.2f})"
            )

    return "\n".join(lines)


def build_compressed_schema_context(
    selected_tables: Sequence[str],
    columns_by_table: Dict[str, List[str]],
    column_relevance: Dict[str, Dict[str, dict]],
    table_relevance: Dict[str, dict],
    joins: Sequence[dict],
    business_metrics: Sequence[dict],
    schema_index: Dict[str, Dict[str, Any]],
) -> List[dict]:
    fragments: List[dict] = []

    for metric in business_metrics:
        fragments.append({
            "table": metric.get("table"),
            "column": metric.get("column"),
            "description": (
                f"Business metric {metric['name']} uses {metric['expression']}. "
                f"Required columns: {', '.join(metric.get('required_columns', []))}."
            ),
            "similarity": round(metric.get("confidence", 0.0), 6),
            "relevance_score": round(metric.get("confidence", 0.0), 6),
            "context_type": "business_metric",
            "compressed": True,
        })

    for join in joins:
        score = round(float(join.get("confidence", 0.0)), 6)
        fragments.append({
            "table": join.get("left_table"),
            "column": join.get("left_column"),
            "description": (
                "Validated join path: "
                f"{join['left_table']}.{join['left_column']} = "
                f"{join['right_table']}.{join['right_column']}."
            ),
            "similarity": score,
            "relevance_score": score,
            "context_type": "join",
            "compressed": True,
        })

    for table_name in selected_tables:
        table = schema_index.get(table_name)
        if not table:
            continue
        table_score = float(table_relevance.get(table_name, {}).get("score", 0.0))
        column_list = columns_by_table.get(table_name, [])
        table_desc = table.get("description") or f"Selected table {table_name}."
        fragments.append({
            "table": table_name,
            "column": None,
            "description": (
                f"Table {table_name}: {table_desc} "
                f"Compressed columns: {', '.join(column_list)}."
            ),
            "similarity": round(table_score, 6),
            "relevance_score": round(table_score, 6),
            "context_type": "table",
            "compressed": True,
        })

        for column_name in column_list:
            column = table["columns"].get(column_name)
            if not column:
                continue
            relevance = column_relevance.get(table_name, {}).get(column_name, {})
            score = float(relevance.get("score", 0.0))
            description = _compact_text(
                column.get("description") or f"Column {column_name} in table {table_name}.",
                max_chars=120,
            )
            reasons = ", ".join(relevance.get("reasons", [])[:2])
            reason_suffix = f" Reasons: {reasons}." if reasons else ""
            fragments.append({
                "table": table_name,
                "column": column_name,
                "description": (
                    f"Column {table_name}.{column_name} | {_column_type(column)}. "
                    f"{description}{reason_suffix}"
                ),
                "similarity": round(score, 6),
                "relevance_score": round(score, 6),
                "context_type": "column",
                "required": "required" in relevance.get("reasons", []),
                "compressed": True,
            })

    fragments.sort(key=lambda item: _fragment_sort_key(item), reverse=True)
    return _limit_fragments(fragments)


def _build_schema_index(schema_metadata: Optional[dict]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for table in (schema_metadata or {}).get("tables", []) or []:
        table_name = _identifier(table.get("table_name"))
        if not table_name:
            continue
        columns: Dict[str, dict] = {}
        column_order: List[str] = []
        for column in table.get("columns", []) or []:
            column_name = _identifier(
                column.get("clean_name") or column.get("name") or column.get("column_name")
            )
            if not column_name or column_name in columns:
                continue
            columns[column_name] = column
            column_order.append(column_name)
        index[table_name] = {
            "raw": table,
            "description": table.get("description") or table.get("llm_context", ""),
            "columns": columns,
            "column_order": column_order,
            "basic_ddl": table.get("basic_ddl", ""),
        }
    return index


def _score_retrieval_context(
    schema_context: Sequence[dict],
    schema_index: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    table_scores: Dict[str, float] = {}
    column_scores: Dict[str, Dict[str, float]] = {}
    selected_fragments: List[dict] = []

    for item in schema_context:
        table = _identifier(item.get("table"))
        if not table or table not in schema_index:
            continue
        score = _retrieval_score(item)
        table_scores[table] = max(table_scores.get(table, 0.0), score)
        column = _identifier(item.get("column"))
        if column and column in schema_index[table]["columns"]:
            column_scores.setdefault(table, {})[column] = max(
                column_scores.setdefault(table, {}).get(column, 0.0),
                score,
            )
        selected_fragments.append(item)

    return {
        "table_scores": table_scores,
        "column_scores": column_scores,
        "fragments": selected_fragments,
    }


def _score_tables(
    q_norm: str,
    query_tokens: set,
    schema_index: Dict[str, Dict[str, Any]],
    retrieval_scores: Dict[str, Any],
) -> Dict[str, dict]:
    scored: Dict[str, dict] = {}
    for table_name, table in schema_index.items():
        score = 0.0
        reasons: List[str] = []

        retrieval_score = retrieval_scores.get("table_scores", {}).get(table_name, 0.0)
        if retrieval_score:
            score += 0.55 * retrieval_score
            reasons.append("retrieval")

        table_phrase = table_name.replace("_", " ")
        if _contains_phrase(q_norm, table_name) or _contains_phrase(q_norm, table_phrase):
            score += 0.65
            reasons.append("explicit_table")

        domain_boost = _domain_table_boost(table_name, q_norm)
        if domain_boost:
            score += domain_boost
            reasons.append("business_keyword")

        table_tokens = _tokens(f"{table_name} {table.get('description', '')}")
        lexical_overlap = _overlap_ratio(query_tokens, table_tokens)
        if lexical_overlap:
            score += 0.20 * lexical_overlap
            reasons.append("table_text_overlap")

        matched_columns = 0
        for column_name, column in table["columns"].items():
            column_text = f"{column_name} {column.get('description', '')}"
            if _tokens(column_text) & query_tokens:
                matched_columns += 1
        if matched_columns:
            score += min(0.25, matched_columns * 0.035)
            reasons.append("column_text_overlap")

        scored[table_name] = {
            "score": round(_clamp(score), 6),
            "raw_score": round(score, 6),
            "reasons": reasons or ["fallback"],
        }
    return scored


def _select_tables(
    table_relevance: Dict[str, dict],
    max_tables: int,
    intent_tables: Optional[Sequence[str]] = None,
) -> List[str]:
    ranked = sorted(
        table_relevance.items(),
        key=lambda item: (item[1].get("score", 0.0), item[0]),
        reverse=True,
    )
    intent = [table for table in _dedupe(intent_tables or []) if table in table_relevance]
    if intent:
        selected = list(intent)
        top_score = ranked[0][1].get("score", 0.0) if ranked else 0.0
        for table, data in ranked:
            if table in selected:
                continue
            score = data.get("score", 0.0)
            if score >= 0.62 and score >= (top_score * 0.85):
                selected.append(table)
            if len(selected) >= max_tables:
                break
        return selected[:max_tables]

    positive = [table for table, data in ranked if data.get("score", 0.0) > 0.20]
    if positive:
        return positive[:max_tables]
    return [table for table, _data in ranked[:max_tables]]


def _intent_tables(q_norm: str, schema_index: Dict[str, Dict[str, Any]]) -> List[str]:
    tables: List[str] = []
    company_signal = _has_named_company_signal(q_norm)
    industry_signal = any(term in q_norm for term in ["industry", "market share", "company", "companies"])

    if "industry_performance" in schema_index and (industry_signal or company_signal):
        tables.append("industry_performance")

    if "m60_level" in schema_index and any(term in q_norm for term in ["m60", "target", "plan", "planned"]):
        tables.append("m60_level")

    actual_signal = any(term in q_norm for term in ["mom", "actual", "sales", "netweight", "date", "day", "volume"])
    if (
        "mom_level" in schema_index
        and actual_signal
        and "market share" not in q_norm
        and "industry" not in q_norm
        and not company_signal
    ):
        tables.append("mom_level")

    return _dedupe(tables)


def _extract_business_metrics(
    q_norm: str,
    selected_tables: Sequence[str],
    schema_index: Dict[str, Dict[str, Any]],
) -> List[dict]:
    metrics: List[dict] = []

    if _contains_phrase(q_norm, "market share"):
        metric = _metric_for_table(
            name="market_share_percentage",
            q_norm=q_norm,
            selected_tables=selected_tables,
            schema_index=schema_index,
            candidate_tables=METRIC_TABLE_PREFERENCES["market_share_percentage"],
            candidate_columns=["netweight_tmt", "total"],
            aggregation="ratio",
            expression_template="SUM({table}.{column}) * 100.0 / SUM(total market {column})",
            confidence=0.94,
            extra_required=["comname", "category"],
        )
        if metric:
            metrics.append(metric)
        return metrics

    if _contains_phrase(q_norm, "target"):
        preferred = ["target_qty_kl", "target_qty_tmt"] if re.search(r"\bkl\b", q_norm) else ["target_qty_tmt", "target_qty_kl"]
        metric = _metric_for_table(
            name="target_quantity",
            q_norm=q_norm,
            selected_tables=selected_tables,
            schema_index=schema_index,
            candidate_tables=METRIC_TABLE_PREFERENCES["target_quantity"],
            candidate_columns=preferred + ["target_quantity_tmtt", "target_round"],
            aggregation="sum",
            expression_template="SUM({table}.{column})",
            confidence=0.90,
        )
        if metric:
            metrics.append(metric)
            return metrics

    if (
        any(term in q_norm for term in ["sales", "actual", "netweight", "quantity", "volume", "performance"])
        or any(term in q_norm for term in ["highest", "lowest", "rank", "top", "bottom"])
    ):
        metric = _metric_for_table(
            name="actual_sales",
            q_norm=q_norm,
            selected_tables=selected_tables,
            schema_index=schema_index,
            candidate_tables=METRIC_TABLE_PREFERENCES["actual_sales"],
            candidate_columns=["netweight_tmt", "actual_round", "total"],
            aggregation="sum",
            expression_template="SUM({table}.{column})",
            confidence=0.88,
        )
        if metric:
            if re.search(r"\bmt\b", q_norm) and metric["column"] == "netweight_tmt":
                metric["expression"] = metric["expression"] + " * 1000 for MT output"
                metric["unit"] = "MT"
            metrics.append(metric)

    if any(term in q_norm for term in ["count", "how many", "number of"]):
        table = selected_tables[0] if selected_tables else None
        if table:
            metrics.append({
                "name": "row_count",
                "table": table,
                "column": "*",
                "aggregation": "count",
                "expression": f"COUNT(*) from {table}",
                "required_columns": [],
                "confidence": 0.86,
                "unit": "rows",
            })

    return metrics


def _metric_for_table(
    name: str,
    q_norm: str,
    selected_tables: Sequence[str],
    schema_index: Dict[str, Dict[str, Any]],
    candidate_tables: Sequence[str],
    candidate_columns: Sequence[str],
    aggregation: str,
    expression_template: str,
    confidence: float,
    extra_required: Optional[Sequence[str]] = None,
) -> Optional[dict]:
    ordered_tables = _dedupe(list(candidate_tables) + list(selected_tables))
    for table_name in ordered_tables:
        if table_name not in schema_index:
            continue
        if selected_tables and table_name not in selected_tables and not _domain_table_boost(table_name, q_norm):
            continue
        column = _first_existing(schema_index[table_name]["columns"], candidate_columns)
        if not column:
            continue
        required = [column]
        for extra in extra_required or []:
            if extra in schema_index[table_name]["columns"] and extra not in required:
                required.append(extra)
        return {
            "name": name,
            "table": table_name,
            "column": column,
            "aggregation": aggregation,
            "expression": expression_template.format(table=table_name, column=column),
            "required_columns": required,
            "confidence": confidence,
            "unit": _metric_unit(column),
        }
    return None


def _required_columns_for_question(
    q_norm: str,
    selected_tables: Sequence[str],
    schema_index: Dict[str, Dict[str, Any]],
    business_metrics: Sequence[dict],
) -> Dict[str, set]:
    required: Dict[str, set] = {table: set() for table in selected_tables}

    for table_name in selected_tables:
        for column in BUSINESS_RULE_COLUMNS.get(table_name, []):
            if column in schema_index.get(table_name, {}).get("columns", {}):
                required[table_name].add(column)

    for metric in business_metrics:
        table = metric.get("table")
        if table in schema_index:
            required.setdefault(table, set())
            for column in metric.get("required_columns", []):
                if column in schema_index[table]["columns"]:
                    required[table].add(column)

    for phrase, candidates in DIMENSION_CANDIDATES.items():
        if not _contains_phrase(q_norm, phrase):
            continue
        for table_name in selected_tables:
            column = _first_existing(schema_index[table_name]["columns"], candidates)
            if column:
                required.setdefault(table_name, set()).add(column)

    if _has_named_company_signal(q_norm):
        for table_name in selected_tables:
            column = _first_existing(schema_index[table_name]["columns"], ["comname", "coname", "company_name"])
            if column:
                required.setdefault(table_name, set()).add(column)

    if any(re.search(rf"\b{product}\b", q_norm) for product in ["hsd", "ms", "lpg", "atf", "sko", "ldo", "fo"]):
        for table_name in selected_tables:
            column = _first_existing(schema_index[table_name]["columns"], ["productname", "materialgroupname", "prod1", "productname_org"])
            if column:
                required.setdefault(table_name, set()).add(column)

    return required


def _plan_relevant_joins(selected_tables: Sequence[str], schema_metadata: Optional[dict]) -> Dict[str, Any]:
    if len(selected_tables) <= 1:
        return {
            "joins": [],
            "join_paths": [],
            "join_confidence": 1.0,
            "join_strategy": "single_table",
        }

    try:
        graph = RelationshipGraphManager(schema_metadata)
        if not graph.has_relationships():
            return {
                "joins": [],
                "join_paths": [],
                "join_confidence": 0.0,
                "join_strategy": "unavailable",
            }
        return JoinPathPlanner(graph).plan_joins(selected_tables)
    except RelationshipPlanningError as exc:
        return {
            "joins": [],
            "join_paths": [],
            "join_confidence": 0.0,
            "join_strategy": "no_path",
            "join_planning_error": str(exc),
        }
    except Exception as exc:
        return {
            "joins": [],
            "join_paths": [],
            "join_confidence": 0.0,
            "join_strategy": "error",
            "join_planning_error": str(exc),
        }


def _include_join_path_tables(
    selected_tables: Sequence[str],
    joins: Sequence[dict],
    schema_index: Dict[str, Dict[str, Any]],
) -> List[str]:
    tables = list(selected_tables)
    for join in joins:
        for table in [join.get("left_table"), join.get("right_table")]:
            if table in schema_index and table not in tables:
                tables.append(table)
    return tables


def _add_join_columns(
    required_columns: Dict[str, set],
    joins: Sequence[dict],
    schema_index: Dict[str, Dict[str, Any]],
) -> None:
    for join in joins:
        left_table = join.get("left_table")
        right_table = join.get("right_table")
        left_column = join.get("left_column")
        right_column = join.get("right_column")
        if left_table in schema_index and left_column in schema_index[left_table]["columns"]:
            required_columns.setdefault(left_table, set()).add(left_column)
        if right_table in schema_index and right_column in schema_index[right_table]["columns"]:
            required_columns.setdefault(right_table, set()).add(right_column)


def _score_columns(
    q_norm: str,
    query_tokens: set,
    schema_index: Dict[str, Dict[str, Any]],
    selected_tables: Sequence[str],
    retrieval_scores: Dict[str, Any],
    required_columns: Dict[str, set],
) -> Dict[str, Dict[str, dict]]:
    scored: Dict[str, Dict[str, dict]] = {}
    for table_name in selected_tables:
        table = schema_index.get(table_name)
        if not table:
            continue
        scored[table_name] = {}
        for order, column_name in enumerate(table["column_order"]):
            column = table["columns"][column_name]
            score = 0.02
            reasons: List[str] = []

            retrieval_score = retrieval_scores.get("column_scores", {}).get(table_name, {}).get(column_name, 0.0)
            if retrieval_score:
                score += 0.45 * retrieval_score
                reasons.append("retrieval")

            column_phrase = column_name.replace("_", " ")
            if _contains_phrase(q_norm, column_name) or _contains_phrase(q_norm, column_phrase):
                score += 0.45
                reasons.append("explicit_column")

            column_tokens = _tokens(f"{column_name} {column.get('description', '')}")
            lexical_overlap = _overlap_ratio(query_tokens, column_tokens)
            if lexical_overlap:
                score += 0.25 * lexical_overlap
                reasons.append("semantic_overlap")

            if column_name in required_columns.get(table_name, set()):
                score += 0.60
                reasons.append("required")

            if _looks_like_identifier(column_name):
                score += 0.03
            if _is_metricish(column_name):
                score += 0.04

            scored[table_name][column_name] = {
                "score": round(_clamp(score), 6),
                "raw_score": round(score, 6),
                "reasons": reasons or ["schema_order"],
                "order": order,
            }
    return scored


def _select_columns_by_table(
    selected_tables: Sequence[str],
    column_relevance: Dict[str, Dict[str, dict]],
    required_columns: Dict[str, set],
    max_columns_per_table: int,
) -> Dict[str, List[str]]:
    selected: Dict[str, List[str]] = {}
    for table_name in selected_tables:
        table_scores = column_relevance.get(table_name, {})
        required = [column for column in required_columns.get(table_name, set()) if column in table_scores]
        ranked = sorted(
            table_scores.items(),
            key=lambda item: (item[1].get("score", 0.0), -item[1].get("order", 0), item[0]),
            reverse=True,
        )

        columns = _dedupe(required)
        for column_name, _data in ranked:
            if len(columns) >= max(max_columns_per_table, len(required), MIN_COLUMNS_PER_TABLE):
                break
            if column_name not in columns:
                if (
                    _data.get("score", 0.0) < 0.12
                    and len(columns) >= max(MIN_COLUMNS_PER_TABLE, len(required))
                ):
                    continue
                columns.append(column_name)

        selected[table_name] = sorted(
            columns,
            key=lambda column: table_scores.get(column, {}).get("order", 10_000),
        )
    return selected


def _trim_to_budget(result: Dict[str, Any], token_budget: int) -> Dict[str, Any]:
    fragments = list(result.get("schema_context", []))
    while fragments and _estimate_tokens_from_context(result.get("schema_ddl", ""), fragments) > token_budget:
        fragments.pop()
    result["schema_context"] = fragments
    result["token_estimate"] = _estimate_tokens_from_context(result.get("schema_ddl", ""), fragments)
    return result


def _safe_schema_context(schema_context: Sequence[dict]) -> List[dict]:
    safe = []
    for item in schema_context:
        if "description" in item:
            safe.append(dict(item))
    return safe


def _build_uncompressed_ddl(
    schema_index: Dict[str, Dict[str, Any]],
    tables: Sequence[str],
) -> str:
    ddls = []
    for table_name in tables:
        table = schema_index.get(table_name)
        if not table:
            continue
        if table.get("basic_ddl"):
            ddls.append(table["basic_ddl"])
            continue
        all_columns = table.get("column_order", [])
        ddls.append(build_compressed_ddl(schema_index, [table_name], {table_name: all_columns}, [], []))
    return "\n\n".join(ddls)


def _estimate_tokens_from_context(schema_ddl: str, schema_context: Sequence[dict]) -> int:
    fragment_text = "\n".join(str(item.get("description", "")) for item in schema_context)
    return estimate_tokens(f"{schema_ddl}\n{fragment_text}".strip())


def _public_table_relevance(table_relevance: Dict[str, dict], selected_tables: Sequence[str]) -> List[dict]:
    return [
        {
            "table": table,
            "score": table_relevance.get(table, {}).get("score", 0.0),
            "reasons": table_relevance.get(table, {}).get("reasons", []),
        }
        for table in selected_tables
    ]


def _public_column_relevance(
    column_relevance: Dict[str, Dict[str, dict]],
    selected_tables: Sequence[str],
    columns_by_table: Dict[str, List[str]],
) -> Dict[str, List[dict]]:
    public: Dict[str, List[dict]] = {}
    for table in selected_tables:
        public[table] = []
        for column in columns_by_table.get(table, []):
            data = column_relevance.get(table, {}).get(column, {})
            public[table].append({
                "column": column,
                "score": data.get("score", 0.0),
                "reasons": data.get("reasons", []),
            })
    return public


def _fragment_sort_key(item: dict) -> Tuple[int, float, str]:
    priority = {
        "business_metric": 4,
        "join": 3,
        "table": 2,
        "column": 1,
    }.get(item.get("context_type"), 0)
    return priority, float(item.get("relevance_score", 0.0) or 0.0), item.get("description", "")


def _limit_fragments(
    fragments: Sequence[dict],
    max_column_fragments_per_table: int = 3,
    max_total_fragments: int = 24,
) -> List[dict]:
    kept: List[dict] = []
    column_fragments: List[dict] = []
    column_counts: Dict[str, int] = {}

    for fragment in fragments:
        if fragment.get("context_type") != "column":
            kept.append(fragment)
        else:
            column_fragments.append(fragment)

    column_fragments.sort(
        key=lambda item: (
            1 if item.get("required") else 0,
            float(item.get("relevance_score", 0.0) or 0.0),
            item.get("description", ""),
        ),
        reverse=True,
    )
    for fragment in column_fragments:
        table = fragment.get("table")
        if column_counts.get(table, 0) >= max_column_fragments_per_table:
            continue
        if len(kept) >= max_total_fragments:
            break
        column_counts[table] = column_counts.get(table, 0) + 1
        kept.append(fragment)

    kept.sort(key=lambda item: _fragment_sort_key(item), reverse=True)
    return kept[:max_total_fragments]


def _domain_table_boost(table_name: str, q_norm: str) -> float:
    if table_name == "industry_performance":
        if any(term in q_norm for term in ["industry", "market share", "company", "companies"]) or _has_named_company_signal(q_norm):
            return 0.65
    if table_name == "m60_level":
        if any(term in q_norm for term in ["m60", "target", "plan", "planned"]):
            return 0.58
    if table_name == "mom_level":
        if any(term in q_norm for term in ["mom", "actual", "sales", "netweight", "date", "day", "volume"]):
            return 0.55
    return 0.0


def _has_named_company_signal(q_norm: str) -> bool:
    return any(re.search(rf"\b{name}\b", q_norm) for name in COMPANY_NAMES)


def _retrieval_score(item: dict) -> float:
    for key in ["rerank_score", "hybrid_score", "retrieval_confidence", "similarity", "vector_score", "text_score"]:
        value = item.get(key)
        if value is None:
            continue
        try:
            return _clamp(float(value))
        except (TypeError, ValueError):
            continue
    return 0.0


def _column_type(column: dict) -> str:
    return str(column.get("pg_type") or column.get("type") or column.get("data_type") or "TEXT")


def _compact_text(text: str, max_chars: int = 120) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 3].rstrip(" ,.;") + "..."


def _metric_unit(column_name: str) -> str:
    if column_name.endswith("_kl"):
        return "KL"
    if column_name.endswith("_tmt"):
        return "TMT"
    if column_name == "*":
        return "rows"
    return "value"


def _first_existing(columns: Dict[str, Any], candidates: Sequence[str]) -> Optional[str]:
    for candidate in candidates:
        clean = _identifier(candidate)
        if clean in columns:
            return clean
    return None


def _looks_like_identifier(column_name: str) -> bool:
    return column_name in {"id", "code", "engine_id"} or column_name.endswith("_id") or column_name.endswith("code")


def _is_metricish(column_name: str) -> bool:
    return any(term in column_name for term in ["qty", "quantity", "weight", "total", "actual", "target", "sum"])


def _overlap_ratio(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left)


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9_&]+", _normalize(text)))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _contains_phrase(normalized_text: str, phrase: str) -> bool:
    normalized_phrase = _normalize(phrase)
    if not normalized_phrase:
        return False
    if len(normalized_phrase) <= 3:
        return bool(re.search(rf"\b{re.escape(normalized_phrase)}\b", normalized_text))
    return normalized_phrase in normalized_text


def _identifier(value: Any) -> Optional[str]:
    if value is None:
        return None
    clean = str(value).strip().lower()
    if not clean:
        return None
    return clean


def _dedupe(values: Iterable[Any]) -> List[str]:
    deduped: List[str] = []
    for value in values:
        clean = _identifier(value)
        if clean and clean not in deduped:
            deduped.append(clean)
    return deduped


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(max(value, low), high)
