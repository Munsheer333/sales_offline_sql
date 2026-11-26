"""
NLPSQL SYSTEM — PHASE 06
Production Orchestrator — ties all phases together
"""

import json
import os
import time
import psycopg2
import logging
from typing import Optional
from config import FAST_LLM_MODEL, FAST_LLM_TIMEOUT_SECONDS, LLM_REPAIR_TIMEOUT_SECONDS, PIPELINE_TIMEOUT_SECONDS
from core.observability import (
    log_event,
    new_request_id,
    observe_retrieval,
    observe_sql_generation,
    observe_validation,
    observed_stage,
    record_cache_event,
    record_pipeline_result,
    text_fingerprint,
    PipelineProfiler,
)
from core.phase1_ingestion import analyze_csv, save_schema_metadata, generate_basic_ddl
from core.phase2_embeddings import (
    retrieve_similar_schema, retrieve_similar_examples,
    setup_pgvector, embed_and_store_schema, embed_and_store_examples, get_local_embedder
    , get_embedding_health
)
from core.phase3_context_compressor import compress_generation_context
from core.phase3_llm_core import generate_sql, self_heal_sql, DEFAULT_MODEL
from core.phase3_query_planner import generate_logical_plan
from core.phase3_sql_compiler import SQLCompilerError, compile_logical_plan
from core.phase4_validator import validate_and_execute
from core.phase4_semantic_coverage import validate_question_sql_coverage
from core.business_entity_catalog import analyze_business_entities
from core.db_pool import pooled_connect
from core.phase2_cross_encoder_reranker import get_cross_encoder_health
from core.phase5_router import classify_complexity, get_complexity_system_prompt, route_question
from core.phase6_query_pattern_engine import (
    get_cached_pattern_result,
    match_query_pattern,
    reject_cached_pattern_result,
    store_pattern_result,
    summarize_pattern_match,
)
from core.phase6_verified_sql_cache import (
    get_verified_sql,
    is_rejected_sql,
    record_query_feedback,
    reject_cache_entry,
    store_candidate,
)
from core.query_normalizer import normalize_shorthand_query, detect_incomplete_question



SYSTEM_PROMPT_PHASE6 = """You are the NLPSQL production orchestrator.

Your responsibilities:
1. Accept natural language questions from business users
2. Route the question through: classify → retrieve context → generate SQL → validate → execute
3. If execution fails, trigger self-healing (up to 3 retries)
4. Log every query with: question, generated SQL, complexity, execution time, row count, success/fail
5. Return structured results including: SQL, result rows, execution metadata, confidence score

Never expose internal system errors directly to users — return friendly error messages instead.
Always log failures for the fine-tuning pipeline.
"""

SCHEMA_PATH = "data/schema_metadata.json"

_planner_instance = None
_assembler_instance = None
_sql_memory_instance = None
_SCHEMA_CACHE: dict = {}


def _get_shared_planner():
    global _planner_instance
    if _planner_instance is None:
        from core.logical_planner import LogicalQueryPlanner
        _planner_instance = LogicalQueryPlanner()
    return _planner_instance


def _get_shared_assembler():
    global _assembler_instance
    if _assembler_instance is None:
        from core.sql_assembler import SQLAssembler
        _assembler_instance = SQLAssembler()
    return _assembler_instance


def _get_shared_sql_memory():
    global _sql_memory_instance
    if _sql_memory_instance is None:
        from core.sql_memory import SQLMemory
        _sql_memory_instance = SQLMemory()
    return _sql_memory_instance


def load_schema(schema_path: str = SCHEMA_PATH) -> Optional[dict]:
    try:
        mtime = os.path.getmtime(schema_path)
        cached = _SCHEMA_CACHE.get(schema_path)
        if cached and cached[0] == mtime:
            return cached[1]
        with open(schema_path) as f:
            data = json.load(f)
        _SCHEMA_CACHE[schema_path] = (mtime, data)
        return data
    except FileNotFoundError:
        return None


def get_relevant_ddl(schema_metadata: dict, relevant_tables: list = None) -> str:
    ddls = []
    for t in schema_metadata.get("tables", []):
        if relevant_tables is None or t["table_name"] in relevant_tables:
            if "basic_ddl" in t:
                ddls.append(t["basic_ddl"])
    return "\n\n".join(ddls)


def summarize_context_compression(compression_result: dict, enabled: bool = True, error: Optional[str] = None) -> dict:
    if not enabled:
        return {
            "enabled": False,
            "error": error,
        }

    columns_by_table = compression_result.get("columns_by_table", {})
    return {
        "enabled": True,
        "selected_tables": compression_result.get("tables", []),
        "selected_column_count": sum(len(columns) for columns in columns_by_table.values()),
        "columns_by_table": {table: len(columns) for table, columns in columns_by_table.items()},
        "business_metrics": [metric.get("name") for metric in compression_result.get("business_metrics", [])],
        "join_count": len(compression_result.get("joins", [])),
        "token_estimate": compression_result.get("token_estimate", 0),
        "raw_token_estimate": compression_result.get("raw_token_estimate", 0),
        "token_budget": compression_result.get("token_budget", 0),
        "compression_ratio": compression_result.get("compression_ratio", 1.0),
        "budget_exceeded": compression_result.get("budget_exceeded", False),
    }


def summarize_routing_decision(routing_decision: dict, enabled: bool = True, error: Optional[str] = None) -> dict:
    if not enabled:
        return {
            "enabled": False,
            "error": error,
        }

    return {
        "enabled": True,
        "routing_mode": routing_decision.get("routing_mode"),
        "llm_used": routing_decision.get("llm_used", False),
        "intent": routing_decision.get("intent"),
        "confidence": routing_decision.get("confidence"),
        "requires_llm_review": routing_decision.get("requires_llm_review", False),
        "source_tables": [item.get("table") for item in routing_decision.get("source_tables", [])],
        "metrics": [item.get("name") for item in routing_decision.get("metrics", [])],
        "aggregations": [item.get("function") for item in routing_decision.get("aggregations", [])],
        "filter_count": len(routing_decision.get("filters", [])),
        "time_range_count": len(routing_decision.get("time_ranges", [])),
        "signals": routing_decision.get("signals", []),
    }


def finish_pipeline_result(result: dict, start_perf: float, request_id: str, conn_str: Optional[str] = None) -> dict:
    elapsed_seconds = time.perf_counter() - start_perf
    generation_mode = result.get("generation_mode", "unknown")
    success = bool(result.get("success", False))
    record_pipeline_result(elapsed_seconds, success, generation_mode)
    
    metrics = PipelineProfiler.get_metrics()
    # Extract internal plan for cache use; strip from serializable output
    _cached_original_plan = metrics.pop("_original_plan", None)
    result["latency_info"] = metrics
    
    import logging
    logger = logging.getLogger("nlpsql.latency_profile")
    logger.info(json.dumps(metrics, default=str))
    
    # Auto-populate intent cache for successful results with a logical plan
    import logging
    logger = logging.getLogger("nlpsql.intent_cache")
    if success and result.get("sql") and generation_mode != "intent_cache_hit":
        try:
            from core.intent_cache import get_intent_cache
            _icache = get_intent_cache()
            logger.debug("Attempting to store in intent cache. plan: %s", _cached_original_plan)
            if _cached_original_plan:
                _icache.store(
                    intent=_cached_original_plan.get("intent", ""),
                    metric=_cached_original_plan.get("metric", ""),
                    filters=_cached_original_plan.get("filters", {}),
                    sql=result["sql"],
                    result=result,
                    dimension=_cached_original_plan.get("dimension"),
                    comparison=_cached_original_plan.get("comparison"),
                    conn_str=conn_str,
                )
                logger.debug("Successfully stored in intent cache database!")
            else:
                logger.debug("No original plan found in metrics; skipping intent cache store.")
        except Exception as exc:
            logger.warning("Failed to store intent cache: %s", exc)

    result.setdefault("observability", {})
    result["observability"].update({
        "request_id": request_id,
        "pipeline_duration_ms": int(elapsed_seconds * 1000),
        "latency_info": metrics,
    })
    log_event(
        "pipeline.finish",
        request_id=request_id,
        success=success,
        generation_mode=generation_mode,
        duration_ms=int(elapsed_seconds * 1000),
        row_count=result.get("row_count", 0),
        latency_info=metrics,
    )
    return result


def log_query(
    conn_str: str,
    question: str,
    sql: str,
    complexity: str,
    execution_time_ms: int,
    row_count: int,
    was_valid: bool,
    error_message: Optional[str] = None
) -> Optional[int]:
    try:
        latency_info = PipelineProfiler.get_metrics()
        conn = pooled_connect(conn_str)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO nlpsql_query_history 
               (question, generated_sql, complexity, execution_time_ms, row_count, was_valid, error_message, latency_info)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (question, sql, complexity, execution_time_ms, row_count, was_valid, error_message, json.dumps(latency_info))
        )
        query_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return query_id
    except Exception:
        return None  # Never let logging crash the main pipeline


def save_feedback(conn_str: str, query_id: int, feedback: int):
    try:
        conn = pooled_connect(conn_str)
        cur = conn.cursor()
        cur.execute(
            "UPDATE nlpsql_query_history SET feedback = %s WHERE id = %s",
            (feedback, query_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        record_query_feedback(conn_str, query_id, feedback)
    except Exception:
        pass


def _store_success_candidate(
    conn_str: str,
    question: str,
    sql: str,
    schema_metadata: dict,
    query_id: Optional[int],
    success: bool,
) -> None:
    if not success:
        return
    store_candidate(
        conn_str=conn_str,
        question=question,
        sql=sql,
        schema_metadata=schema_metadata,
        source_query_id=query_id,
    )


def _rejected_sql_response(
    conn_str: str,
    question: str,
    sql: str,
    complexity: str,
    start_total: float,
    start_perf: float,
    request_id: str,
    logical_plan: Optional[dict] = None,
) -> dict:
    total_ms = int((time.time() - start_total) * 1000)
    error = (
        "The generated SQL matches an answer previously marked incorrect. "
        "The system blocked it instead of returning the same wrong answer. "
        "Add corrected Gold SQL for this question or refine the business rule."
    )
    query_id = log_query(
        conn_str=conn_str,
        question=question,
        sql=sql,
        complexity=complexity,
        execution_time_ms=total_ms,
        row_count=0,
        was_valid=False,
        error_message=error,
    )
    record_cache_event("rejected_sql", "blocked")
    return finish_pipeline_result({
        "id": query_id,
        "success": False,
        "question": question,
        "sql": sql,
        "complexity": complexity,
        "generation_mode": "rejected_sql_blocked",
        "review_status": "rejected",
        "reviewed_sql_reused": False,
        "logical_plan": logical_plan,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "execution_time_ms": total_ms,
        "healed": False,
        "warnings": [],
        "error": error,
        "schema_context_used": 0,
        "raw_schema_context_used": 0,
        "routing": {"enabled": False, "reason": "rejected_sql_blocked"},
        "context_compression": {"enabled": False, "reason": "rejected_sql_blocked"},
        "query_pattern": {"matched": False, "reason": "rejected_sql_blocked"},
        "examples_used": 0,
    }, start_perf, request_id)


def _analyze_entities_safely(question: str, conn_str: str, schema_metadata: dict) -> dict:
    try:
        return analyze_business_entities(question, conn_str=conn_str, schema_metadata=schema_metadata)
    except Exception as exc:
        log_event("entity_catalog.error", level=logging.WARNING, error=str(exc))
        return {"matches": [], "unresolved": [], "catalog_counts": {}, "error": str(exc)}


def _semantic_coverage(
    question: str,
    sql: str,
    schema_metadata: dict,
    logical_plan: Optional[dict] = None,
    entity_analysis: Optional[dict] = None,
) -> dict:
    return validate_question_sql_coverage(
        question=question,
        sql=sql,
        schema_metadata=schema_metadata,
        logical_plan=logical_plan,
        entity_analysis=entity_analysis,
    )


def _semantic_failure_response(
    conn_str: str,
    question: str,
    sql: str,
    complexity: str,
    semantic_report: dict,
    start_total: float,
    start_perf: float,
    request_id: str,
    logical_plan: Optional[dict] = None,
) -> dict:
    internal_error = "Generated SQL was blocked because it does not cover all requested constraints."
    clarification = _semantic_clarification_message(semantic_report)
    total_ms = int((time.time() - start_total) * 1000)
    query_id = log_query(conn_str, question, sql, complexity, total_ms, 0, False, internal_error)
    log_event("semantic_coverage.blocked", request_id=request_id, errors=semantic_report.get("errors", []))
    return finish_pipeline_result({
        "id": query_id,
        "success": False,
        "question": question,
        "sql": sql,
        "complexity": complexity,
        "generation_mode": "needs_clarification",
        "review_status": "clarification_required",
        "needs_clarification": True,
        "assistant_message": clarification,
        "logical_plan": logical_plan,
        "semantic_coverage": semantic_report,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "execution_time_ms": total_ms,
        "healed": False,
        "warnings": [],
        "error": None,
        "schema_context_used": 0,
        "raw_schema_context_used": 0,
        "routing": {"enabled": False, "reason": "semantic_coverage_blocked"},
        "context_compression": {"enabled": False, "reason": "semantic_coverage_blocked"},
        "query_pattern": {"matched": False, "reason": "semantic_coverage_blocked"},
        "examples_used": 0,
    }, start_perf, request_id)


def _semantic_clarification_message(semantic_report: dict) -> str:
    errors = semantic_report.get("errors") or []
    unresolved = [
        item.get("message", "").replace("Unknown ", "", 1)
        for item in errors
        if item.get("code") == "UNRESOLVED_ENTITY"
    ]
    if unresolved:
        return (
            f"I could not match {', '.join(unresolved[:3])} to the loaded dataset. "
            "Please use the dataset label or add the missing qualifier."
        )
    missing = [
        item.get("message", "").replace("SQL dropped required filter ", "", 1)
        for item in errors
        if item.get("code") == "MISSING_FILTER"
    ]
    if missing:
        return (
            f"I found more than one possible interpretation for {', '.join(missing[:3])}. "
            "Please confirm the intended company, SBU, product, zone, state, district, month, or fiscal year."
        )
    return (
        "I found more than one safe interpretation for this request. Please add the intended output level "
        "or filter, such as company, SBU, product, zone, state, district, month, or fiscal year."
    )


def _remaining_timeout(deadline: float, stage_cap: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("NL2SQL pipeline latency budget exhausted.")
    return max(min(stage_cap, remaining), 0.1)


def _generation_failure_response(
    question: str,
    error: Exception,
    start_perf: float,
    request_id: str,
    schema_metadata: Optional[dict] = None,
    entity_analysis: Optional[dict] = None,
) -> dict:
    log_event("sql_generation.error", level=logging.ERROR, request_id=request_id, error=str(error))
    clarification = _build_clarification_message(question, schema_metadata, entity_analysis)
    return finish_pipeline_result({
        "success": False,
        "question": question,
        "sql": "",
        "generation_mode": "needs_clarification",
        "review_status": "clarification_required",
        "needs_clarification": True,
        "assistant_message": clarification,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "error": None,
    }, start_perf, request_id)


def _build_clarification_message(
    question: str,
    schema_metadata: Optional[dict] = None,
    entity_analysis: Optional[dict] = None,
) -> str:
    """Return a useful local response when SQL cannot be proven safely."""
    entity_analysis = entity_analysis or {"matches": [], "unresolved": []}
    unresolved = entity_analysis.get("unresolved") or []
    if unresolved:
        labels = ", ".join(f"{item.get('type')}: {item.get('value')}" for item in unresolved[:3])
        return (
            f"I need one correction before calculating the answer. I could not match {labels} "
            "to the loaded dataset. Please use the dataset label or add the missing qualifier."
        )

    route = route_question(question, schema_metadata=schema_metadata, use_llm_fallback=False)
    metric_names = [item.get("name") for item in route.get("metrics", []) if item.get("name")]
    matched_entities = [
        f"{item.get('type')} {item.get('value')}"
        for item in entity_analysis.get("matches", [])
        if item.get("type") and item.get("value")
    ]
    context = ", ".join(matched_entities[:4])
    if metric_names and context:
        return (
            f"I recognized {', '.join(metric_names)} for {context}, but the requested comparison or "
            "breakdown is ambiguous. Please specify the output level, such as company, SBU, product, "
            "zone, state, district, month, or fiscal year."
        )
    if metric_names:
        return (
            f"I recognized {', '.join(metric_names)}, but I need the business scope to calculate it "
            "accurately. Please include a company, SBU, product, zone, state, district, month, or fiscal year."
        )
    return (
        "I need one more detail to answer accurately from the loaded datasets. Please state the metric "
        "and scope, for example total sales, target quantity, market share, or growth by company, SBU, "
        "product, zone, state, district, month, or fiscal year."
    )


def _try_fast_llm_generation(
    question: str,
    schema_context: list,
    few_shot_examples: list,
    relevant_ddl: str,
    schema_metadata: dict,
    entity_analysis: dict,
    primary_model: str,
    request_id: str,
) -> Optional[dict]:
    """Use a small local model for one bounded retry after primary generation stalls."""
    if not FAST_LLM_MODEL or FAST_LLM_MODEL == primary_model:
        return None
    try:
        with observed_stage("sql_generation.fast_fallback", request_id=request_id):
            result = generate_sql(
                question=question,
                schema_context=schema_context,
                few_shot_examples=few_shot_examples,
                full_schema_ddl=relevant_ddl,
                model=FAST_LLM_MODEL,
                schema_metadata=schema_metadata,
                entity_matches=entity_analysis.get("matches"),
                deadline=time.monotonic() + FAST_LLM_TIMEOUT_SECONDS,
            )
        result["generation_mode"] = f"fast_llm_fallback:{result.get('generation_mode', 'unknown')}"
        result["primary_model"] = primary_model
        return result
    except Exception as exc:
        log_event("sql_generation.fast_fallback_error", level=logging.WARNING, request_id=request_id, error=str(exc))
        return None


def _try_early_pattern_fast_path(
    question: str,
    conn_str: str,
    schema_metadata: dict,
    entity_analysis: dict,
    start_total: float,
    start_perf: float,
    request_id: str,
) -> Optional[dict]:
    """Execute known deterministic templates before loading the embedding model."""
    try:
        pattern_match = match_query_pattern(
            question=question,
            schema_metadata=schema_metadata,
            similar_examples=[],
            entity_matches=entity_analysis.get("matches"),
        )
        if not pattern_match.get("matched"):
            return None
        sql = pattern_match["sql"]
        if is_rejected_sql(conn_str, question, sql):
            record_cache_event("query_pattern", "rejected")
            return None
        semantic_report = _semantic_coverage(
            question,
            sql,
            schema_metadata,
            entity_analysis=entity_analysis,
        )
        if not semantic_report["valid"]:
            record_cache_event("query_pattern", "semantic_rejected")
            return None
        with observed_stage("validation.query_pattern_early", request_id=request_id) as timer:
            exec_result = validate_and_execute(
                sql=sql,
                schema_metadata=schema_metadata,
                conn_str=conn_str,
                llm_heal_fn=None,
                schema_ddl="",
                max_retries=1,
                question=question,
            )
        observe_validation(timer.elapsed_seconds, "success" if exec_result.get("success") else "error")
        if not exec_result.get("success"):
            return None

        store_pattern_result(conn_str, pattern_match, exec_result)
        total_ms = int((time.time() - start_total) * 1000)
        final_sql = exec_result.get("sql", sql)
        query_id = log_query(
            conn_str, question, final_sql, pattern_match.get("complexity", "GENERAL"),
            total_ms, exec_result.get("row_count", 0), True, None,
        )
        _store_success_candidate(conn_str, question, final_sql, schema_metadata, query_id, True)
        return finish_pipeline_result({
            "id": query_id,
            "success": True,
            "question": question,
            "sql": final_sql,
            "complexity": pattern_match.get("complexity", "GENERAL"),
            "generation_mode": "query_pattern_template",
            "review_status": "candidate",
            "reviewed_sql_reused": False,
            "logical_plan": None,
            "columns": exec_result.get("columns", []),
            "rows": exec_result.get("rows", []),
            "row_count": exec_result.get("row_count", 0),
            "execution_time_ms": total_ms,
            "healed": False,
            "warnings": exec_result.get("warnings", []),
            "error": None,
            "routing": summarize_routing_decision(pattern_match.get("routing", {})),
            "schema_context_used": 0,
            "raw_schema_context_used": 0,
            "context_compression": {"enabled": False, "reason": "query_pattern_template"},
            "query_pattern": summarize_pattern_match(pattern_match, cache_stored=True),
            "parameterized_sql": pattern_match.get("parameterized_sql"),
            "sql_params": pattern_match.get("params", []),
            "semantic_coverage": semantic_report,
            "entity_analysis": entity_analysis,
            "examples_used": 0,
        }, start_perf, request_id, conn_str=conn_str)
    except Exception as exc:
        log_event("query_pattern.early_error", level=logging.WARNING, request_id=request_id, error=str(exc))
        return None


def _try_early_compiler_fast_path(
    question: str,
    conn_str: str,
    schema_metadata: dict,
    entity_analysis: dict,
    start_total: float,
    start_perf: float,
    request_id: str,
) -> Optional[dict]:
    """Compile provable logical plans before retrieval and local model loading."""
    try:
        logical_plan = generate_logical_plan(
            question,
            schema_metadata=schema_metadata,
            entity_matches=entity_analysis.get("matches"),
        )
        if logical_plan.get("is_off_topic"):
            return None
        compiled = compile_logical_plan(logical_plan, schema_metadata=schema_metadata)
        sql = compiled.sql
        if is_rejected_sql(conn_str, question, sql):
            return None
        semantic_report = _semantic_coverage(
            question,
            sql,
            schema_metadata,
            logical_plan=logical_plan,
            entity_analysis=entity_analysis,
        )
        if not semantic_report["valid"]:
            return None
        with observed_stage("validation.compiler_early", request_id=request_id) as timer:
            exec_result = validate_and_execute(
                sql=sql,
                schema_metadata=schema_metadata,
                conn_str=conn_str,
                llm_heal_fn=None,
                schema_ddl="",
                max_retries=1,
                question=question,
            )
        observe_validation(timer.elapsed_seconds, "success" if exec_result.get("success") else "error")
        if not exec_result.get("success"):
            return None

        total_ms = int((time.time() - start_total) * 1000)
        final_sql = exec_result.get("sql", sql)
        query_id = log_query(conn_str, question, final_sql, "DETERMINISTIC", total_ms, exec_result.get("row_count", 0), True, None)
        _store_success_candidate(conn_str, question, final_sql, schema_metadata, query_id, True)
        return finish_pipeline_result({
            "id": query_id,
            "success": True,
            "question": question,
            "sql": final_sql,
            "complexity": "DETERMINISTIC",
            "generation_mode": "deterministic_compiler",
            "review_status": "candidate",
            "reviewed_sql_reused": False,
            "logical_plan": logical_plan,
            "join_confidence": logical_plan.get("join_confidence"),
            "join_strategy": logical_plan.get("join_strategy"),
            "parameterized_sql": compiled.parameterized_sql,
            "sql_params": list(compiled.params),
            "compiler_warnings": list(compiled.warnings),
            "columns": exec_result.get("columns", []),
            "rows": exec_result.get("rows", []),
            "row_count": exec_result.get("row_count", 0),
            "execution_time_ms": total_ms,
            "healed": False,
            "warnings": exec_result.get("warnings", []),
            "error": None,
            "routing": {"enabled": False, "reason": "deterministic_compiler"},
            "schema_context_used": 0,
            "raw_schema_context_used": 0,
            "context_compression": {"enabled": False, "reason": "deterministic_compiler"},
            "query_pattern": {"matched": False, "reason": "deterministic_compiler"},
            "semantic_coverage": semantic_report,
            "entity_analysis": entity_analysis,
            "examples_used": 0,
        }, start_perf, request_id, conn_str=conn_str)
    except SQLCompilerError:
        return None
    except Exception as exc:
        log_event("compiler.early_error", level=logging.WARNING, request_id=request_id, error=str(exc))
        return None


def get_query_history(conn_str: str, limit: int = 50) -> list:
    try:
        conn = pooled_connect(conn_str)
        cur = conn.cursor()
        cur.execute(
            """SELECT id, question, generated_sql, complexity, execution_time_ms,
                      row_count, was_valid, error_message, feedback, created_at
               FROM nlpsql_query_history
               ORDER BY created_at DESC
               LIMIT %s""",
            (limit,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [{
            "id": r[0], "question": r[1], "sql": r[2], "complexity": r[3],
            "execution_time_ms": r[4], "row_count": r[5], "was_valid": r[6],
            "error": r[7], "feedback": r[8], "created_at": str(r[9])
        } for r in rows]
    except Exception:
        return []


def run_pipeline(
    question: str,
    conn_str: str,
    model: str = DEFAULT_MODEL,
    embedder=None,
    schema_path: str = SCHEMA_PATH,
    session_id: Optional[str] = "default_session"
) -> dict:
    """Main pipeline: question → SQL → results."""
    PipelineProfiler.reset()
    from core.conversational_context import resolve_conversational_query
    if session_id:
        question = resolve_conversational_query(question, session_id)

    clarification_msg = detect_incomplete_question(question)
    if clarification_msg:
        start_perf = time.perf_counter()
        request_id = new_request_id()
        return finish_pipeline_result({
            "success": False,
            "question": question,
            "sql": "",
            "generation_mode": "needs_clarification",
            "review_status": "clarification_required",
            "needs_clarification": True,
            "assistant_message": clarification_msg,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "error": None,
        }, start_perf, request_id)

    original_question = question  # preserve for intent cache key consistency
    question = normalize_shorthand_query(question)
    start_total = time.time()
    start_perf = time.perf_counter()


    deadline = time.monotonic() + PIPELINE_TIMEOUT_SECONDS
    request_id = new_request_id()
    question_fp = text_fingerprint(question)
    log_event(
        "pipeline.start",
        request_id=request_id,
        question_fingerprint=question_fp,
        model=model,
    )

    with observed_stage("schema.load", request_id=request_id):
        schema_metadata = load_schema(schema_path)
    if not schema_metadata:
        return finish_pipeline_result(
            {"success": False, "error": "Schema not loaded. Please run data ingestion first.", "sql": ""},
            start_perf,
            request_id,
        )

    # Verified SQL reuse is intentionally before embeddings and retrieval. Only
    # approved SQL is eligible, and current data is always fetched afresh.
    with observed_stage("cache.verified_sql.lookup", request_id=request_id):
        verified_sql = get_verified_sql(conn_str, question, schema_metadata)
    if verified_sql:
        record_cache_event("verified_sql", "hit")
        semantic_report = _semantic_coverage(question, verified_sql["sql"], schema_metadata)
        if not semantic_report["valid"]:
            reject_cache_entry(conn_str, verified_sql["id"], "semantic_coverage_failed")
            record_cache_event("verified_sql", "semantic_invalidated")
            verified_sql = None
    if verified_sql:
        with observed_stage("validation.verified_sql_cache", request_id=request_id) as validation_timer:
            exec_result = validate_and_execute(
                sql=verified_sql["sql"],
                schema_metadata=schema_metadata,
                conn_str=conn_str,
                llm_heal_fn=None,
                schema_ddl="",
                max_retries=1,
                question=question,
                skip_explain=True,
            )
        observe_validation(
            validation_timer.elapsed_seconds,
            "success" if exec_result.get("success", False) else "error",
        )
        if exec_result.get("success", False):
            total_ms = int((time.time() - start_total) * 1000)
            sql = exec_result.get("sql", verified_sql["sql"])
            query_id = log_query(
                conn_str=conn_str,
                question=question,
                sql=sql,
                complexity="VERIFIED_CACHE",
                execution_time_ms=total_ms,
                row_count=exec_result.get("row_count", 0),
                was_valid=True,
                error_message=None,
            )
            _store_success_candidate(conn_str, question, sql, schema_metadata, query_id, True)
            return finish_pipeline_result({
                "id": query_id,
                "success": True,
                "question": question,
                "sql": sql,
                "complexity": "VERIFIED_CACHE",
                "generation_mode": "verified_sql_cache_hit",
                "review_status": "verified",
                "reviewed_sql_reused": True,
                "logical_plan": None,
                "columns": exec_result.get("columns", []),
                "rows": exec_result.get("rows", []),
                "row_count": exec_result.get("row_count", 0),
                "execution_time_ms": total_ms,
                "cache_hit": True,
                "healed": False,
                "warnings": exec_result.get("warnings", []),
                "error": None,
                "routing": {"enabled": False, "reason": "verified_sql_cache_hit"},
                "schema_context_used": 0,
                "raw_schema_context_used": 0,
                "context_compression": {"enabled": False, "reason": "verified_sql_cache_hit"},
                "query_pattern": {"matched": False, "reason": "verified_sql_cache_hit"},
                "examples_used": 0,
            }, start_perf, request_id, conn_str=conn_str)
        reject_cache_entry(
            conn_str,
            verified_sql["id"],
            exec_result.get("error") or "verified_sql_failed_validation_or_execution",
        )
        record_cache_event("verified_sql", "invalidated")
    else:
        record_cache_event("verified_sql", "miss")

    t_ent_start = time.perf_counter()
    entity_analysis = _analyze_entities_safely(question, conn_str, schema_metadata)
    PipelineProfiler.set_ms("entity_extraction_ms", time.perf_counter() - t_ent_start)

    # --- NEW RESOLUTION, CLASSIFICATION, AND PLANNING FLOW ---
    t_plan_start = time.perf_counter()
    planner_confidence = 0.0
    plan = None
    _original_plan = None
    try:
        planner = _get_shared_planner()
        plan = planner.plan(question)
        # Build cache key plan from original_question (before normalize_shorthand_query)
        # to ensure consistent keys across paraphrases
        if original_question == question:
            _original_plan = plan
        else:
            _original_plan = planner.plan(original_question)
        
        # Merge dynamically matched database entities into planner filters
        plans_to_update = (plan,) if _original_plan is plan else (plan, _original_plan)
        for p in plans_to_update:
            if p and "filters" in p:
                p_dim = p.get("dimension")
                p_comp_dim = p.get("comparison_dimension") or p_dim
                p_comp_set = {str(c).lower() for c in p.get("comparison", [])}
                for match in entity_analysis.get("matches", []):
                    etype = match.get("type")
                    val = match.get("value")
                    if etype and val:
                        # Skip if etype is the dimension/comparison dimension or if value is being compared
                        if etype == p_dim or etype == p_comp_dim or str(val).lower() in p_comp_set:
                            continue
                        if etype not in p["filters"] or not p["filters"][etype]:
                            p["filters"][etype] = val
                if p_dim and p_dim in p["filters"]:
                    del p["filters"][p_dim]
                if p_comp_dim and p_comp_dim in p["filters"]:
                    del p["filters"][p_comp_dim]
        
        intent_info = planner.classifier.classify(original_question)
        planner_confidence = intent_info["confidence"]
        
        PipelineProfiler.set_ms("router_ms", time.perf_counter() - t_plan_start)
    except Exception as exc:
        log_event("planner.early_error", level=logging.WARNING, request_id=request_id, error=str(exc))

    # Record confidence routing metadata in profiler
    if planner_confidence > 0.95:
        _conf_route = "high_deterministic"
    elif planner_confidence >= 0.70:
        _conf_route = "medium_memory"
    else:
        _conf_route = "low_llm"
    PipelineProfiler.set_value("confidence_route", _conf_route)
    PipelineProfiler.set_value("planner_confidence", round(planner_confidence, 4))
    if plan:
        PipelineProfiler.set_value("_original_plan", _original_plan)

    # --- INTENT CACHE LOOKUP (semantic key: intent + metric + sorted entities) ---
    intent_cache_hit = None
    _cache_plan = _original_plan if plan else None
    if _cache_plan:
        try:
            from core.intent_cache import get_intent_cache
            _icache = get_intent_cache()
            intent_cache_hit = _icache.lookup(
                intent=_cache_plan.get("intent", ""),
                metric=_cache_plan.get("metric", ""),
                filters=_cache_plan.get("filters", {}),
                dimension=_cache_plan.get("dimension"),
                comparison=_cache_plan.get("comparison"),
                conn_str=conn_str,
            )
            if intent_cache_hit:
                record_cache_event("intent_cache", "hit")
                cached_sql = intent_cache_hit["sql"]
                if not is_rejected_sql(conn_str, question, cached_sql):
                    with observed_stage("validation.intent_cache", request_id=request_id) as timer:
                        exec_result = validate_and_execute(
                            sql=cached_sql,
                            schema_metadata=schema_metadata,
                            conn_str=conn_str,
                            llm_heal_fn=None,
                            schema_ddl="",
                            max_retries=1,
                            question=question,
                            skip_explain=True,
                        )
                    observe_validation(timer.elapsed_seconds, "success" if exec_result.get("success") else "error")
                    if exec_result.get("success"):
                        total_ms = int((time.time() - start_total) * 1000)
                        final_sql = exec_result.get("sql", cached_sql)
                        query_id = log_query(conn_str, question, final_sql, "INTENT_CACHE", total_ms, exec_result.get("row_count", 0), True, None)
                        _store_success_candidate(conn_str, question, final_sql, schema_metadata, query_id, True)
                        PipelineProfiler.set_value("intent_cache_hit", True)
                        return finish_pipeline_result({
                            "id": query_id,
                            "success": True,
                            "question": question,
                            "sql": final_sql,
                            "complexity": "INTENT_CACHE",
                            "generation_mode": "intent_cache_hit",
                            "review_status": "cached",
                            "reviewed_sql_reused": True,
                            "logical_plan": plan,
                            "columns": exec_result.get("columns", []),
                            "rows": exec_result.get("rows", []),
                            "row_count": exec_result.get("row_count", 0),
                            "execution_time_ms": total_ms,
                            "cache_hit": True,
                            "healed": False,
                            "warnings": exec_result.get("warnings", []),
                            "error": None,
                            "routing": {"enabled": False, "reason": "intent_cache_hit"},
                            "schema_context_used": 0,
                            "raw_schema_context_used": 0,
                            "context_compression": {"enabled": False, "reason": "intent_cache_hit"},
                            "query_pattern": {"matched": False, "reason": "intent_cache_hit"},
                            "entity_analysis": plan.get("filters", {}),
                            "examples_used": 0,
                        }, start_perf, request_id)
                else:
                    record_cache_event("intent_cache", "rejected_sql")
            else:
                record_cache_event("intent_cache", "miss")
        except Exception as exc:
            log_event("intent_cache.lookup_error", level=logging.WARNING, request_id=request_id, error=str(exc))

    PipelineProfiler.set_value("intent_cache_hit", False)
        
    # Route: confidence > 95% (0.95): Planner -> SQL Builder (No LLM)
    if plan and planner_confidence > 0.95:
        try:
            assembler = _get_shared_assembler()
            assembled_sql = assembler.assemble(plan, question=question)
            
            # Check rejected
            if not is_rejected_sql(conn_str, question, assembled_sql):
                with observed_stage("validation.compiler_early", request_id=request_id) as timer:
                    exec_result = validate_and_execute(
                        sql=assembled_sql,
                        schema_metadata=schema_metadata,
                        conn_str=conn_str,
                        llm_heal_fn=None,
                        schema_ddl="",
                        max_retries=1,
                        question=question,
                    )
                observe_validation(timer.elapsed_seconds, "success" if exec_result.get("success") else "error")
                
                if exec_result.get("success", False):
                    total_ms = int((time.time() - start_total) * 1000)
                    final_sql = exec_result.get("sql", assembled_sql)
                    query_id = log_query(conn_str, question, final_sql, "DETERMINISTIC", total_ms, exec_result.get("row_count", 0), True, None)
                    _store_success_candidate(conn_str, question, final_sql, schema_metadata, query_id, True)
                    
                    return finish_pipeline_result({
                        "id": query_id,
                        "success": True,
                        "question": question,
                        "sql": final_sql,
                        "complexity": "DETERMINISTIC",
                        "generation_mode": "deterministic_compiler",
                        "review_status": "candidate",
                        "reviewed_sql_reused": False,
                        "logical_plan": plan,
                        "columns": exec_result.get("columns", []),
                        "rows": exec_result.get("rows", []),
                        "row_count": exec_result.get("row_count", 0),
                        "execution_time_ms": total_ms,
                        "healed": False,
                        "warnings": exec_result.get("warnings", []),
                        "error": None,
                        "routing": {"enabled": False, "reason": "deterministic_compiler"},
                        "schema_context_used": 0,
                        "raw_schema_context_used": 0,
                        "context_compression": {"enabled": False, "reason": "deterministic_compiler"},
                        "query_pattern": {"matched": False, "reason": "deterministic_compiler"},
                        "entity_analysis": plan.get("filters", {}),
                        "examples_used": 0,
                    }, start_perf, request_id, conn_str=conn_str)
                else:
                    log_event("compiler.early_validation_failed", level=logging.WARNING, request_id=request_id, error=exec_result.get("error"))
        except Exception as exc:
            log_event("compiler.assembly_error", level=logging.WARNING, request_id=request_id, error=str(exc))

    # Route: confidence between 70%-95% (0.70 <= confidence <= 0.95): Planner -> Example SQL Memory
    elif plan and 0.70 <= planner_confidence <= 0.95:
        try:
            sql_mem = _get_shared_sql_memory()
            memory_match = sql_mem.retrieve(
                query=question,
                intent=plan.get("intent", "TOTAL_SALES"),
                entities=plan.get("filters", {})
            )
            if memory_match:
                memory_sql = memory_match["sql"]
                with observed_stage("validation.memory_example", request_id=request_id) as timer:
                    exec_result = validate_and_execute(
                        sql=memory_sql,
                        schema_metadata=schema_metadata,
                        conn_str=conn_str,
                        llm_heal_fn=None,
                        schema_ddl="",
                        max_retries=1,
                        question=question,
                    )
                observe_validation(timer.elapsed_seconds, "success" if exec_result.get("success") else "error")
                
                if exec_result.get("success", False):
                    total_ms = int((time.time() - start_total) * 1000)
                    final_sql = exec_result.get("sql", memory_sql)
                    query_id = log_query(conn_str, question, final_sql, "MEMORY_EXAMPLE", total_ms, exec_result.get("row_count", 0), True, None)
                    _store_success_candidate(conn_str, question, final_sql, schema_metadata, query_id, True)
                    return finish_pipeline_result({
                        "id": query_id,
                        "success": True,
                        "question": question,
                        "sql": final_sql,
                        "complexity": "MEMORY_EXAMPLE",
                        "generation_mode": "gold_fast_path",
                        "review_status": "gold",
                        "reviewed_sql_reused": True,
                        "logical_plan": plan,
                        "columns": exec_result.get("columns", []),
                        "rows": exec_result.get("rows", []),
                        "row_count": exec_result.get("row_count", 0),
                        "execution_time_ms": total_ms,
                        "healed": False,
                        "warnings": exec_result.get("warnings", []),
                        "error": None,
                        "routing": {"enabled": False, "reason": "memory_example_hit"},
                        "schema_context_used": 0,
                        "raw_schema_context_used": 0,
                        "context_compression": {"enabled": False, "reason": "memory_example_hit"},
                        "query_pattern": {"matched": False, "reason": "memory_example_hit"},
                        "examples_used": 1,
                    }, start_perf, request_id, conn_str=conn_str)
                else:
                    log_event("memory.validation_failed", level=logging.WARNING, request_id=request_id, error=exec_result.get("error"))
            else:
                log_event("memory.no_match", level=logging.INFO, request_id=request_id, question=question)
        except Exception as exc:
            log_event("memory.retrieve_error", level=logging.WARNING, request_id=request_id, error=str(exc))

    # Deterministic pattern & compiler fast paths BEFORE loading heavy embedding models
    early_pattern_result = _try_early_pattern_fast_path(
        question=question,
        conn_str=conn_str,
        schema_metadata=schema_metadata,
        entity_analysis=entity_analysis,
        start_total=start_total,
        start_perf=start_perf,
        request_id=request_id,
    )
    if early_pattern_result is not None:
        return early_pattern_result

    early_compiler_result = _try_early_compiler_fast_path(
        question=question,
        conn_str=conn_str,
        schema_metadata=schema_metadata,
        entity_analysis=entity_analysis,
        start_total=start_total,
        start_perf=start_perf,
        request_id=request_id,
    )
    if early_compiler_result is not None:
        return early_compiler_result

    if embedder is None:
        embedder = get_local_embedder()

    # --- 1. CHECK GOLD FAST-PATH FIRST ---
    # Retrieve similar examples immediately to see if we have a direct/gold match
    run_fast_paths = (planner_confidence >= 0.70)
    if run_fast_paths:
        t_ret_start = time.perf_counter()
        with observed_stage("retrieval.examples", request_id=request_id) as retrieval_timer:
            few_shot_examples = retrieve_similar_examples(question, conn_str, embedder, top_k=5)
        PipelineProfiler.add_ms("retrieval_ms", time.perf_counter() - t_ret_start)
        few_shot_examples = [
            example for example in few_shot_examples
            if not is_rejected_sql(conn_str, question, example.get("sql", ""))
        ]
        observe_retrieval("examples", retrieval_timer.elapsed_seconds, len(few_shot_examples))
        log_event(
            "retrieval.examples",
            request_id=request_id,
            result_count=len(few_shot_examples),
            top_similarity=few_shot_examples[0].get("similarity") if few_shot_examples else None,
        )
    else:
        few_shot_examples = []
    
    if few_shot_examples and few_shot_examples[0]["similarity"] > 0.98:
        sql = few_shot_examples[0]["sql"]
        complexity = few_shot_examples[0]["complexity"] or "GENERAL"
        record_cache_event("gold_path", "candidate")
        
        # Skip LLM call and validation for 100% matches
        if few_shot_examples[0]["similarity"] > 0.999:
            semantic_report = _semantic_coverage(question, sql, schema_metadata, entity_analysis=entity_analysis)
            if not semantic_report["valid"]:
                record_cache_event("gold_path", "semantic_rejected")
                sql = ""
        if sql and few_shot_examples[0]["similarity"] > 0.999:
            record_cache_event("gold_path", "hit")
            with observed_stage("validation.gold_fast_path", request_id=request_id) as validation_timer:
                exec_result = validate_and_execute(
                    sql=sql,
                    schema_metadata=schema_metadata,
                    conn_str=conn_str,
                    llm_heal_fn=None, # No need to heal a gold query
                    schema_ddl="",
                    max_retries=1,
                    question=question,
                )
            observe_validation(
                validation_timer.elapsed_seconds,
                "success" if exec_result.get("success", False) else "error",
            )
            total_ms = int((time.time() - start_total) * 1000)
            
            # Log the query to history
            query_id = log_query(
                conn_str=conn_str,
                question=question,
                sql=sql,
                complexity=complexity,
                execution_time_ms=total_ms,
                row_count=exec_result.get("row_count", 0),
                was_valid=exec_result.get("success", False),
                error_message=exec_result.get("error")
            )
            _store_success_candidate(
                conn_str, question, sql, schema_metadata, query_id, exec_result.get("success", False)
            )
            
            return finish_pipeline_result({
                "id": query_id,
                "success": exec_result.get("success", False),
                "question": question,
                "sql": sql,
                "complexity": complexity,
                "generation_mode": "gold_fast_path",
                "review_status": "gold",
                "reviewed_sql_reused": False,
                "logical_plan": None,
                "columns": exec_result.get("columns", []),
                "rows": exec_result.get("rows", []),
                "row_count": exec_result.get("row_count", 0),
                "execution_time_ms": total_ms,
                "healed": False,
                "warnings": exec_result.get("warnings", []),
                "error": exec_result.get("error"),
                "schema_context_used": 0,
                "raw_schema_context_used": 0,
                "routing": {"enabled": False, "reason": "gold_fast_path"},
                "context_compression": {"enabled": False, "reason": "gold_fast_path"},
                "query_pattern": {"matched": False, "reason": "gold_fast_path"},
                "examples_used": 1,
                "note": "Deterministic match found in Gold Standard memory."
            }, start_perf, request_id, conn_str=conn_str)
        record_cache_event("gold_path", "miss")

    # --- 1B. REUSABLE QUERY PATTERN FAST-PATH ---
    # This extends the gold path: high-confidence known query shapes can be
    # rendered from PostgreSQL templates and still pass through validation.
    query_pattern_summary = {"matched": False}
    try:
        if not run_fast_paths:
            raise StopIteration("Bypass fast path query pattern")
        with observed_stage("query_pattern.match", request_id=request_id) as pattern_timer:
            pattern_match = match_query_pattern(
                question=question,
                schema_metadata=schema_metadata,
                similar_examples=few_shot_examples,
                entity_matches=entity_analysis.get("matches"),
            )
        query_pattern_summary = summarize_pattern_match(pattern_match)
        log_event(
            "query_pattern.match",
            request_id=request_id,
            matched=pattern_match.get("matched", False),
            template_id=pattern_match.get("template_id"),
            confidence=pattern_match.get("confidence"),
            duration_ms=pattern_timer.elapsed_ms,
        )

        if pattern_match.get("matched") and is_rejected_sql(conn_str, question, pattern_match.get("sql", "")):
            record_cache_event("query_pattern", "rejected")
            query_pattern_summary["rejected_sql_blocked"] = True
            query_pattern_summary["fallback"] = "regular_pipeline"

        if pattern_match.get("matched") and not query_pattern_summary.get("rejected_sql_blocked"):
            pattern_semantic_report = _semantic_coverage(
                question,
                pattern_match["sql"],
                schema_metadata,
                entity_analysis=entity_analysis,
            )
            if not pattern_semantic_report["valid"]:
                record_cache_event("query_pattern", "semantic_rejected")
                query_pattern_summary["semantic_coverage"] = pattern_semantic_report
                query_pattern_summary["fallback"] = "regular_pipeline"
                pattern_match["matched"] = False

        if pattern_match.get("matched") and not query_pattern_summary.get("rejected_sql_blocked"):
            with observed_stage("cache.query_pattern.lookup", request_id=request_id):
                cached_result = get_cached_pattern_result(
                    conn_str,
                    pattern_match["execution_fingerprint"]
                )
            if cached_result:
                record_cache_event("query_pattern", "hit")
                with observed_stage("validation.query_pattern_cache", request_id=request_id) as validation_timer:
                    cached_exec = validate_and_execute(
                        sql=cached_result.get("sql", pattern_match["sql"]),
                        schema_metadata=schema_metadata,
                        conn_str=conn_str,
                        llm_heal_fn=None,
                        schema_ddl="",
                        max_retries=1,
                        question=question,
                        skip_explain=True,
                    )
                observe_validation(
                    validation_timer.elapsed_seconds,
                    "success" if cached_exec.get("success", False) else "error",
                )
                if not cached_exec.get("success", False):
                    reject_cached_pattern_result(conn_str, pattern_match["execution_fingerprint"])
                    record_cache_event("query_pattern", "invalidated")
                    cached_result = None
            if cached_result:
                total_ms = int((time.time() - start_total) * 1000)
                query_pattern_summary = summarize_pattern_match(pattern_match, cache_hit=True)
                cached_sql = cached_exec.get("sql", cached_result.get("sql", pattern_match["sql"]))
                query_id = log_query(
                    conn_str=conn_str,
                    question=question,
                    sql=cached_sql,
                    complexity=pattern_match.get("complexity", "GENERAL"),
                    execution_time_ms=total_ms,
                    row_count=cached_exec.get("row_count", 0),
                    was_valid=True,
                    error_message=None
                )
                _store_success_candidate(conn_str, question, cached_sql, schema_metadata, query_id, True)
                return finish_pipeline_result({
                    "id": query_id,
                    "success": True,
                    "question": question,
                    "sql": cached_sql,
                    "complexity": pattern_match.get("complexity", "GENERAL"),
                    "generation_mode": "query_pattern_cache_hit",
                    "review_status": "verified_pattern",
                    "reviewed_sql_reused": False,
                    "logical_plan": None,
                    "columns": cached_exec.get("columns", []),
                    "rows": cached_exec.get("rows", []),
                    "row_count": cached_exec.get("row_count", 0),
                    "execution_time_ms": total_ms,
                    "cached_execution_time_ms": cached_result.get("execution_time_ms"),
                    "cache_hit": True,
                    "healed": False,
                    "warnings": cached_result.get("warnings", []),
                    "error": None,
                    "routing": summarize_routing_decision(pattern_match.get("routing", {})),
                    "schema_context_used": 0,
                    "raw_schema_context_used": 0,
                    "context_compression": {"enabled": False, "reason": "query_pattern_cache_hit"},
                    "query_pattern": query_pattern_summary,
                    "parameterized_sql": cached_result.get("parameterized_sql"),
                    "sql_params": cached_result.get("sql_params", []),
                    "examples_used": len(few_shot_examples),
                }, start_perf, request_id, conn_str=conn_str)
            record_cache_event("query_pattern", "miss")

            with observed_stage("validation.query_pattern", request_id=request_id) as validation_timer:
                pattern_exec = validate_and_execute(
                    sql=pattern_match["sql"],
                    schema_metadata=schema_metadata,
                    conn_str=conn_str,
                    llm_heal_fn=None,
                    schema_ddl="",
                    max_retries=2,
                    question=question,
                )
            observe_validation(
                validation_timer.elapsed_seconds,
                "success" if pattern_exec.get("success", False) else "error",
            )
            if pattern_exec.get("success", False):
                with observed_stage("cache.query_pattern.store", request_id=request_id):
                    cache_stored = store_pattern_result(conn_str, pattern_match, pattern_exec)
                record_cache_event("query_pattern", "store" if cache_stored else "store_error")
                query_pattern_summary = summarize_pattern_match(pattern_match, cache_stored=cache_stored)
                total_ms = int((time.time() - start_total) * 1000)
                query_id = log_query(
                    conn_str=conn_str,
                    question=question,
                    sql=pattern_exec.get("sql", pattern_match["sql"]),
                    complexity=pattern_match.get("complexity", "GENERAL"),
                    execution_time_ms=total_ms,
                    row_count=pattern_exec.get("row_count", 0),
                    was_valid=True,
                    error_message=None
                )
                _store_success_candidate(
                    conn_str,
                    question,
                    pattern_exec.get("sql", pattern_match["sql"]),
                    schema_metadata,
                    query_id,
                    True,
                )
                return finish_pipeline_result({
                    "id": query_id,
                    "success": True,
                    "question": question,
                    "sql": pattern_exec.get("sql", pattern_match["sql"]),
                    "complexity": pattern_match.get("complexity", "GENERAL"),
                    "generation_mode": "query_pattern_template",
                    "review_status": "candidate",
                    "reviewed_sql_reused": False,
                    "logical_plan": None,
                    "columns": pattern_exec.get("columns", []),
                    "rows": pattern_exec.get("rows", []),
                    "row_count": pattern_exec.get("row_count", 0),
                    "execution_time_ms": total_ms,
                    "cache_hit": False,
                    "healed": pattern_exec.get("healed", False),
                    "warnings": pattern_exec.get("warnings", []),
                    "error": pattern_exec.get("error"),
                    "routing": summarize_routing_decision(pattern_match.get("routing", {})),
                    "schema_context_used": 0,
                    "raw_schema_context_used": 0,
                    "context_compression": {"enabled": False, "reason": "query_pattern_template"},
                    "query_pattern": query_pattern_summary,
                    "parameterized_sql": pattern_match.get("parameterized_sql"),
                    "sql_params": pattern_match.get("params", []),
                    "validation_report": pattern_exec.get("validation_report"),
                    "validation_reports": pattern_exec.get("validation_reports", []),
                    "examples_used": len(few_shot_examples),
                }, start_perf, request_id, conn_str=conn_str)

            query_pattern_summary["execution_error"] = pattern_exec.get("error")
            query_pattern_summary["fallback"] = "regular_pipeline"
    except StopIteration:
        # Low-confidence bypass — skip pattern matching silently
        query_pattern_summary = {"matched": False, "reason": "low_confidence_bypass", "fallback": "regular_pipeline"}
    except Exception as exc:
        record_cache_event("query_pattern", "error")
        log_event(
            "query_pattern.error",
            level=logging.ERROR,
            request_id=request_id,
            error=str(exc),
        )
        query_pattern_summary = {"matched": False, "error": str(exc), "fallback": "regular_pipeline"}

    # --- 2. REGULAR PIPELINE ---
    # Phase 5: deterministic routing first; LLM classifier remains available but is not used by default.
    t_router_start = time.perf_counter()
    try:
        with observed_stage("routing", request_id=request_id):
            routing_decision = route_question(
                question=question,
                schema_metadata=schema_metadata,
                model=model,
                use_llm_fallback=False
            )
        complexity = routing_decision.get("complexity") or classify_complexity(question, model=model)
        routing_summary = summarize_routing_decision(routing_decision)
    except Exception as exc:
        complexity = classify_complexity(question, model=model)
        routing_decision = {}
        routing_summary = summarize_routing_decision({}, enabled=False, error=str(exc))
    PipelineProfiler.set_ms("router_ms", time.perf_counter() - t_router_start)

    t_ret_start2 = time.perf_counter()
    with observed_stage("retrieval.schema", request_id=request_id) as schema_retrieval_timer:
        schema_context = retrieve_similar_schema(question, conn_str, embedder, top_k=8)
    PipelineProfiler.add_ms("retrieval_ms", time.perf_counter() - t_ret_start2)
    observe_retrieval("schema", schema_retrieval_timer.elapsed_seconds, len(schema_context))
    log_event(
        "retrieval.schema",
        request_id=request_id,
        result_count=len(schema_context),
        top_similarity=schema_context[0].get("similarity") if schema_context else None,
    )

    # Deduplicate examples for LLM prompt if they have the same SQL
    seen_sql = set()
    unique_examples = []
    for ex in few_shot_examples:
        if ex["sql"] not in seen_sql:
            unique_examples.append(ex)
            seen_sql.add(ex["sql"])

    # Compress retrieved schema into prompt-compatible schema fragments and DDL.
    # This is intentionally post-retrieval so the retrieval API stays unchanged.
    raw_schema_context = schema_context
    try:
        with observed_stage("context_compression", request_id=request_id):
            compression_result = compress_generation_context(
                question=question,
                schema_metadata=schema_metadata,
                schema_context=raw_schema_context
            )
        schema_context = compression_result.get("schema_context") or raw_schema_context
        relevant_tables = compression_result.get("tables") or [
            item["table"] for item in raw_schema_context if item.get("table")
        ]
        relevant_ddl = compression_result.get("schema_ddl") or get_relevant_ddl(schema_metadata, relevant_tables)
        context_compression = summarize_context_compression(compression_result)
    except Exception as exc:
        relevant_tables = list({item["table"] for item in raw_schema_context if item.get("table")})
        if not relevant_tables:
            relevant_tables = [t["table_name"] for t in schema_metadata.get("tables", [])]
        relevant_ddl = get_relevant_ddl(schema_metadata, relevant_tables)
        schema_context = raw_schema_context
        context_compression = summarize_context_compression({}, enabled=False, error=str(exc))

    # Phase 3: Generate SQL
    try:
        with observed_stage("sql_generation", request_id=request_id) as generation_timer:
            gen_result = generate_sql(
                question=question,
                schema_context=schema_context,
                few_shot_examples=unique_examples,
                full_schema_ddl=relevant_ddl,
                model=model,
                schema_metadata=schema_metadata,
                entity_matches=entity_analysis.get("matches"),
                deadline=deadline,
            )
    except Exception as exc:
        gen_result = _try_fast_llm_generation(
            question=question,
            schema_context=schema_context,
            few_shot_examples=unique_examples,
            relevant_ddl=relevant_ddl,
            schema_metadata=schema_metadata,
            entity_analysis=entity_analysis,
            primary_model=model,
            request_id=request_id,
        )
        if gen_result is None:
            return _generation_failure_response(
                question,
                exc,
                start_perf,
                request_id,
                schema_metadata=schema_metadata,
                entity_analysis=entity_analysis,
            )
    observe_sql_generation(
        generation_timer.elapsed_seconds,
        gen_result.get("generation_mode", "unknown"),
        "success" if gen_result.get("sql") else "error",
    )
    sql = gen_result["sql"]
    semantic_report = _semantic_coverage(
        question,
        sql,
        schema_metadata,
        logical_plan=gen_result.get("logical_plan"),
        entity_analysis=entity_analysis,
    )
    if not semantic_report["valid"]:
        return _semantic_failure_response(
            conn_str, question, sql, complexity, semantic_report,
            start_total, start_perf, request_id, gen_result.get("logical_plan"),
        )
    if is_rejected_sql(conn_str, question, sql):
        rejected_sql = sql
        record_cache_event("rejected_sql", "repair_attempt")
        try:
            with observed_stage("repair.llm.rejected_sql", request_id=request_id):
                sql = self_heal_sql(
                    rejected_sql,
                    (
                        "The user explicitly marked this SQL as semantically incorrect for the question: "
                        f"{question!r}. Generate one semantically different SELECT query that answers the "
                        "question using the provided schema. Do not return the rejected SQL again."
                    ),
                    relevant_ddl,
                    model=model,
                    timeout_seconds=_remaining_timeout(deadline, LLM_REPAIR_TIMEOUT_SECONDS),
                )
            if is_rejected_sql(conn_str, question, sql):
                return _rejected_sql_response(
                    conn_str=conn_str,
                    question=question,
                    sql=sql,
                    complexity=complexity,
                    start_total=start_total,
                    start_perf=start_perf,
                    request_id=request_id,
                    logical_plan=gen_result.get("logical_plan"),
                )
            gen_result["generation_mode"] = "rejected_sql_llm_repair"
            gen_result["rejected_sql"] = rejected_sql
        except Exception:
            return _rejected_sql_response(
                conn_str=conn_str,
                question=question,
                sql=rejected_sql,
                complexity=complexity,
                start_total=start_total,
                start_perf=start_perf,
                request_id=request_id,
                logical_plan=gen_result.get("logical_plan"),
            )

    # Phase 4: Validate + self-heal + execute
    def heal_fn(broken_sql, error_msg, schema_ddl):
        return self_heal_sql(
            broken_sql,
            error_msg,
            schema_ddl,
            model=model,
            timeout_seconds=_remaining_timeout(deadline, LLM_REPAIR_TIMEOUT_SECONDS),
        )

    with observed_stage("validation.regular", request_id=request_id) as validation_timer:
        exec_result = validate_and_execute(
            sql=sql,
            schema_metadata=schema_metadata,
            conn_str=conn_str,
            llm_heal_fn=heal_fn,
            schema_ddl=relevant_ddl,
            max_retries=3,
            question=question,
        )
    observe_validation(
        validation_timer.elapsed_seconds,
        "success" if exec_result.get("success", False) else "error",
    )

    total_ms = int((time.time() - start_total) * 1000)
    final_sql = exec_result.get("sql", sql)
    semantic_report = _semantic_coverage(
        question,
        final_sql,
        schema_metadata,
        logical_plan=gen_result.get("logical_plan"),
        entity_analysis=entity_analysis,
    )
    if exec_result.get("success", False) and not semantic_report["valid"]:
        return _semantic_failure_response(
            conn_str, question, final_sql, complexity, semantic_report,
            start_total, start_perf, request_id, gen_result.get("logical_plan"),
        )
    if exec_result.get("success", False) and is_rejected_sql(conn_str, question, final_sql):
        return _rejected_sql_response(
            conn_str=conn_str,
            question=question,
            sql=final_sql,
            complexity=complexity,
            start_total=start_total,
            start_perf=start_perf,
            request_id=request_id,
            logical_plan=gen_result.get("logical_plan"),
        )

    # Phase 6: Log
    query_id = log_query(
        conn_str=conn_str,
        question=question,
        sql=final_sql,
        complexity=complexity,
        execution_time_ms=total_ms,
        row_count=exec_result.get("row_count", 0),
        was_valid=exec_result.get("success", False),
        error_message=exec_result.get("error")
    )
    _store_success_candidate(
        conn_str,
        question,
        final_sql,
        schema_metadata,
        query_id,
        exec_result.get("success", False),
    )

    return finish_pipeline_result({
        "id": query_id,
        "success": exec_result.get("success", False),
        "question": question,
        "sql": final_sql,
        "complexity": complexity,
        "generation_mode": gen_result.get("generation_mode", "unknown"),
        "review_status": "candidate" if exec_result.get("success", False) else "unreviewed",
        "reviewed_sql_reused": False,
        "logical_plan": gen_result.get("logical_plan"),
        "join_confidence": gen_result.get("join_confidence"),
        "join_strategy": gen_result.get("join_strategy"),
        "compiler_error": gen_result.get("compiler_error"),
        "planner_error": gen_result.get("planner_error"),
        "parameterized_sql": gen_result.get("parameterized_sql"),
        "sql_params": gen_result.get("sql_params"),
        "compiler_warnings": gen_result.get("compiler_warnings", []),
        "columns": exec_result.get("columns", []),
        "rows": exec_result.get("rows", []),
        "row_count": exec_result.get("row_count", 0),
        "execution_time_ms": total_ms,
        "healed": exec_result.get("healed", False),
        "warnings": exec_result.get("warnings", []),
        "error": exec_result.get("error"),
        "routing": routing_summary,
        "schema_context_used": len(schema_context),
        "raw_schema_context_used": len(raw_schema_context),
        "context_compression": context_compression,
        "query_pattern": query_pattern_summary,
        "examples_used": len(few_shot_examples)
        ,"semantic_coverage": semantic_report
        ,"entity_analysis": entity_analysis
        ,"embedding_health": get_embedding_health()
        ,"reranker_health": get_cross_encoder_health()
    }, start_perf, request_id, conn_str=conn_str)
