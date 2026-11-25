"""
NLPSQL SYSTEM — PHASE 03
Offline LLM Core — SQL Generator with RAG & Few-Shot
Uses Ollama for 100% offline LLM inference.
"""

import json
import requests
import time
from typing import Any, Dict, List, Optional
from config import (
    CURRENT_FINANCIAL_YEAR,
    LLM_GENERATION_TIMEOUT_SECONDS,
    LLM_MAX_REPAIR_TOKENS,
    LLM_MAX_SQL_TOKENS,
    LLM_MODEL,
    LLM_REPAIR_TIMEOUT_SECONDS,
    OLLAMA_GENERATE_URL,
)
from core.phase3_query_planner import generate_logical_plan
from core.observability import PipelineProfiler
from core.phase3_sql_compiler import SQLCompilerError, compile_logical_plan

OLLAMA_URL = OLLAMA_GENERATE_URL
DEFAULT_MODEL = LLM_MODEL

# ─────────────────────────────────────────────
# MASTER SYSTEM PROMPT — The core SQL generator
# ─────────────────────────────────────────────
SYSTEM_PROMPT_PHASE3 = f"""You are an expert PostgreSQL query generator for an MNC-grade internal analytics system called NLPSQL.

You will receive:
1. SCHEMA CONTEXT — table names, column names, types, and semantic descriptions
2. FEW-SHOT EXAMPLES — similar NL→SQL pairs retrieved from a vector database
3. USER QUESTION — a natural language question from a business user

Your task is to generate a syntactically correct, semantically accurate, and optimized PostgreSQL query.

=== POSTGRESQL GENERATION GUIDELINES ===
- If the user question is off-topic, conversational, a greeting, or asking general concepts (e.g. "what is SQL", "how are you", "who are you", "what is meant by SQL"), you MUST output exactly: OFF_TOPIC: Please ask a database-related question regarding office, sales, or industry performance metrics.
- Otherwise, output RAW SQL ONLY.
- NO explanation. NO code fences. NO markdown.
- NO 'TOP' (use LIMIT).
- NO 'GETDATE()' (use NOW()).
- NO square brackets [].
- ALWAYS qualify columns using table name (table.column) in fully lowercase.
- ALWAYS use fully lowercase table names (m60_level, mom_level, industry_performance) and column names. DO NOT use double quotes or square brackets around names in your SQL queries.

=== CRITICAL BUSINESS RULES & LOGICS ===
1. **Category Filtering**: While querying the "industry_performance" table, ALWAYS exclude the letter 'O' from the category field while retaining uncategorized rows: `(industry_performance.category != 'O' OR industry_performance.category IS NULL)`.
2. **SBU Exclusions on mom_level**: When querying "mom_level", always exclude common/non-operational business units in the WHERE clause: sbu_name != '0' AND sbu_name NOT IN ('Common', 'Mumbai Ref', 'Renewable Energy', 'Visakh Ref').
3. **Retail SBU Condition**: When the Retail SBU is specified, use `sbu_name = 'RETAIL'` for `industry_performance` and `sbu_name = 'Retail'` for `mom_level`.
4. **Unit of Measure (UOM) Unit Conversion**: By default, display values in TMT (Thousand Metric Tonnes). If "MT" (Metric Tonnes) is specified in the prompt, convert and display accordingly by multiplying the TMT column by 1000 (e.g., netweight_tmt * 1000).
5. **Day/Date Range Filtering**: When a date, period, or range is specified, use a BETWEEN condition on "day_id" (e.g., day_id BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD').
6. **Fiscal Year (FY) Logic**: If the fiscal year is explicitly mentioned (e.g., 2021-2022), filter by it (e.g., fiscal_year = '2021-2022').
7. **Market Share Calculation**: If the user question explicitly asks for "market share" (e.g., lowest market share, highest market share, or market share of HPCL), you MUST explicitly calculate the market share percentage: `(company_sales * 100.0) / total_market_sales`. Do NOT simply compute total sales or assume that the lowest/highest sales is implicitly the answer. Use CTEs to calculate the individual company sales sum and the overall total market sales sum, and then divide them accordingly. A named-company filter such as `comname = 'HPCL'` belongs only in the company-sales numerator CTE; NEVER apply it to the total-market denominator CTE. If the question asks which month has the lowest or highest market share, group both numerator and denominator by `month_name`, join them on `month_name`, and order the calculated monthly percentage.
   Treat "market contribution" as the same percentage calculation. For zone contribution, group the numerator by `zone_name`, divide each zone total by the full matching market total, and do not filter or group by SBU when the user requests all SBUs.
8. **Current Financial Year Logic**: If the user asks for the current fiscal year, this financial year, current FY, or similar, filter with `fiscal_year = '{CURRENT_FINANCIAL_YEAR}'`.
   If the user asks for LY or last year, filter with the previous fiscal year.
9. **Company Name Column**: When filtering or selecting the company name (e.g., 'IOCL', 'HPCL', 'BPCL', 'SHELL', 'SMA', 'OIL') in the "industry_performance" table, ALWAYS use the column name `comname` (e.g., `comname = 'IOCL'`), NOT the column `company_name` (which contains classifications like 'MPSU' or 'PVT').
10. **Company-Specific Sales Source**: If a named company such as HPCL, IOCL, BPCL, SHELL, SMA, or OIL is present, query `industry_performance` and include `comname = '<COMPANY>'`. Do NOT use `mom_level` because it cannot represent a company constraint.
11. **Sales Growth Comparison**: If the user asks for sales difference or sales growth percentage between two fiscal years, calculate both fiscal-year totals, return their difference, and calculate percentage growth as `(current_sales - previous_sales) * 100.0 / NULLIF(previous_sales, 0)`. Filter both years with `fiscal_year IN (...)`.
12. **Product Growth Thresholds**: If the user asks for products whose sales growth exceeded or fell below a percentage between two fiscal years, query `mom_level`, group by `productname`, calculate both fiscal-year totals and percentage growth, and enforce the requested percentage threshold with `HAVING`. Do not return a plain product total.
=== OUTPUT FORMAT ===
Return ONLY the raw SQL (or the OFF_TOPIC message if off-topic). No explanation. No markdown. No preamble.
"""


SYSTEM_PROMPT_PLAN_TO_SQL = f"""You are an expert PostgreSQL query compiler for NLPSQL.

You will receive:
1. SCHEMA CONTEXT - table names, columns, types, and semantic descriptions
2. FEW-SHOT EXAMPLES - similar natural-language to SQL pairs
3. USER QUESTION - the original business question
4. LOGICAL QUERY PLAN JSON - the planner output with source tables, metrics, filters, aggregations, group-by, ordering, and limit

Your task is to compile the LOGICAL QUERY PLAN JSON into syntactically valid PostgreSQL SQL.

Rules:
- Output RAW SQL ONLY.
- NO explanation. NO code fences. NO markdown.
- NO 'TOP' (use LIMIT).
- NO 'GETDATE()' (use NOW()).
- NO square brackets [].
- ALWAYS use fully lowercase table names and column names.
- DO NOT use double quotes or square brackets around identifiers.
- Only generate SELECT or WITH queries.
- If the logical plan says is_off_topic=true, output exactly: OFF_TOPIC: Please ask a database-related question regarding office, sales, or industry performance metrics.

Business rules:
1. When querying industry_performance, always include `(category != 'O' OR category IS NULL)`.
2. When querying mom_level, always include sbu_name != '0' AND sbu_name NOT IN ('Common', 'Mumbai Ref', 'Renewable Energy', 'Visakh Ref').
3. When Retail SBU is specified, use sbu_name = 'RETAIL' for industry_performance and sbu_name = 'Retail' for mom_level.
4. Default weight unit is TMT. If MT is requested, convert netweight_tmt by multiplying by 1000.
5. Date ranges on mom_level use day_id BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD'.
6. Explicit fiscal years filter fiscal_year.
7. Market share must be calculated as company sales times 100.0 divided by total market sales, preferably using CTEs and CROSS JOIN.
8. Current fiscal year terms must filter with fiscal_year = '{CURRENT_FINANCIAL_YEAR}'.
9. Company filters in industry_performance must use comname, not company_name.
10. Named-company sales queries must use industry_performance with a comname filter. Do not use mom_level for named-company questions because it has no company dimension.
11. Sales difference or growth questions between two fiscal years must calculate both yearly totals, their difference, and percentage growth. Filter both years with fiscal_year IN (...).
12. Product growth threshold questions must use mom_level, group by productname, calculate both yearly totals and growth percentage, and enforce the percentage threshold with HAVING.
"""


def build_rag_prompt(
    question: str,
    schema_context: List[dict],
    few_shot_examples: List[dict],
    full_schema_ddl: str
) -> str:
    schema_block = "=== SCHEMA CONTEXT ===\n"
    schema_block += full_schema_ddl + "\n\n"
    schema_block += "=== RELEVANT SCHEMA FRAGMENTS (by semantic similarity) ===\n"
    for item in schema_context:
        schema_block += f"- {item['description']}\n"

    examples_block = "\n=== FEW-SHOT EXAMPLES (similar past queries) ===\n"
    for i, ex in enumerate(few_shot_examples, 1):
        examples_block += f"\nExample {i} [{ex['complexity']}]:\n"
        examples_block += f"Q: {ex['question']}\n"
        examples_block += f"SQL:\n{ex['sql']}\n"

    return (
        schema_block
        + examples_block
        + f"\n=== USER QUESTION ===\n{question}\n\n"
        + "Generate the PostgreSQL query:"
    )


def build_plan_sql_prompt(
    question: str,
    logical_plan: Dict[str, Any],
    schema_context: List[dict],
    few_shot_examples: List[dict],
    full_schema_ddl: str
) -> str:
    schema_block = "=== SCHEMA CONTEXT ===\n"
    schema_block += full_schema_ddl + "\n\n"
    schema_block += "=== RELEVANT SCHEMA FRAGMENTS (by semantic similarity) ===\n"
    for item in schema_context:
        schema_block += f"- {item['description']}\n"

    examples_block = "\n=== FEW-SHOT EXAMPLES (similar past queries) ===\n"
    for i, ex in enumerate(few_shot_examples, 1):
        examples_block += f"\nExample {i} [{ex['complexity']}]:\n"
        examples_block += f"Q: {ex['question']}\n"
        examples_block += f"SQL:\n{ex['sql']}\n"

    plan_json = json.dumps(logical_plan, indent=2, sort_keys=True)
    return (
        schema_block
        + examples_block
        + f"\n=== USER QUESTION ===\n{question}\n\n"
        + f"=== LOGICAL QUERY PLAN JSON ===\n{plan_json}\n\n"
        + "Compile the logical plan into PostgreSQL SQL:"
    )


def call_ollama(
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.1,
    system_prompt: str = SYSTEM_PROMPT_PHASE3,
    num_predict: int = LLM_MAX_SQL_TOKENS,
    timeout_seconds: float = LLM_GENERATION_TIMEOUT_SECONDS,
) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system_prompt,
        "stream": False,
        "keep_alive": -1,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            "top_p": 0.9,
            "repeat_penalty": 1.1
        }
    }
    t_start = time.perf_counter()
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout_seconds)
        resp.raise_for_status()
        PipelineProfiler.add_ms("llm_ms", time.perf_counter() - t_start)
        return resp.json().get("response", "").strip()
    except requests.exceptions.ConnectionError:
        PipelineProfiler.add_ms("llm_ms", time.perf_counter() - t_start)
        raise ConnectionError(
            "Ollama is not running. Start it with: ollama serve\n"
            "Then pull a model: ollama pull sqlcoder"
        )
    except Exception as e:
        PipelineProfiler.add_ms("llm_ms", time.perf_counter() - t_start)
        error_detail = ""
        try:
            # Try to get the error message from the JSON response if available
            if hasattr(e, 'response') and e.response is not None:
                error_detail = f" - {e.response.text}"
        except:
            pass
        raise RuntimeError(f"Ollama call failed for model '{model}': {e}{error_detail}")


def clean_sql_output(raw: str) -> str:
    """Strip markdown fences, conversational text, and preambles."""
    raw = raw.strip()
    if raw.startswith("OFF_TOPIC"):
        return raw
    
    # 1. Strip markdown code blocks
    if "```" in raw:
        # Extract content between triple backticks if they exist
        import re
        matches = re.findall(r'```(?:sql)?\s*(.*?)\s*```', raw, re.DOTALL | re.IGNORECASE)
        if matches:
            raw = matches[0]
        else:
            raw = raw.replace("```sql", "").replace("```", "")

    # 2. Find the first SELECT or WITH (start of actual SQL)
    upper_raw = raw.upper()
    select_pos = upper_raw.find("SELECT")
    with_pos = upper_raw.find("WITH")
    
    start_pos = -1
    if select_pos != -1 and with_pos != -1:
        start_pos = min(select_pos, with_pos)
    else:
        start_pos = max(select_pos, with_pos)
        
    if start_pos != -1:
        raw = raw[start_pos:]

    # 3. Final cleanup of trailing comments or punctuation
    raw = raw.split(';')[0] + ';' if ';' in raw else raw
    return raw.strip()


def generate_sql_legacy(
    question: str,
    schema_context: List[dict],
    few_shot_examples: List[dict],
    full_schema_ddl: str,
    model: str = DEFAULT_MODEL,
    deadline: Optional[float] = None,
) -> dict:
    prompt = build_rag_prompt(question, schema_context, few_shot_examples, full_schema_ddl)
    raw_output = call_ollama(
        prompt,
        model=model,
        system_prompt=SYSTEM_PROMPT_PHASE3,
        timeout_seconds=_remaining_timeout(deadline, LLM_GENERATION_TIMEOUT_SECONDS),
    )
    sql = clean_sql_output(raw_output)
    return {
        "question": question,
        "sql": sql,
        "prompt_used": prompt,
        "model": model,
        "generation_mode": "legacy_direct",
        "logical_plan": None
    }


def generate_sql_from_plan(
    question: str,
    logical_plan: Dict[str, Any],
    schema_context: List[dict],
    few_shot_examples: List[dict],
    full_schema_ddl: str,
    model: str = DEFAULT_MODEL,
    deadline: Optional[float] = None,
) -> dict:
    prompt = build_plan_sql_prompt(
        question=question,
        logical_plan=logical_plan,
        schema_context=schema_context,
        few_shot_examples=few_shot_examples,
        full_schema_ddl=full_schema_ddl
    )
    raw_output = call_ollama(
        prompt,
        model=model,
        temperature=0.05,
        system_prompt=SYSTEM_PROMPT_PLAN_TO_SQL,
        num_predict=LLM_MAX_SQL_TOKENS,
        timeout_seconds=_remaining_timeout(deadline, LLM_GENERATION_TIMEOUT_SECONDS),
    )
    sql = clean_sql_output(raw_output)
    return {
        "question": question,
        "sql": sql,
        "prompt_used": prompt,
        "model": model,
        "generation_mode": "planned",
        "logical_plan": logical_plan
    }


def _is_select_sql(sql: str) -> bool:
    sql_upper = (sql or "").strip().upper()
    return sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")


def generate_sql(
    question: str,
    schema_context: List[dict],
    few_shot_examples: List[dict],
    full_schema_ddl: str,
    model: str = DEFAULT_MODEL,
    schema_metadata: Optional[dict] = None,
    use_logical_planner: bool = True,
    entity_matches: Optional[List[dict]] = None,
    deadline: Optional[float] = None,
) -> dict:
    if not use_logical_planner:
        return generate_sql_legacy(
            question=question,
            schema_context=schema_context,
            few_shot_examples=few_shot_examples,
            full_schema_ddl=full_schema_ddl,
            model=model,
            deadline=deadline,
        )

    logical_plan = None
    planner_error = None
    compiler_error = None

    try:
        logical_plan = generate_logical_plan(
            question=question,
            schema_context=schema_context,
            schema_metadata=schema_metadata,
            entity_matches=entity_matches,
        )

        if logical_plan.get("is_off_topic"):
            return {
                "question": question,
                "sql": "OFF_TOPIC: Please ask a database-related question regarding office, sales, or industry performance metrics.",
                "prompt_used": json.dumps(logical_plan, indent=2, sort_keys=True),
                "model": model,
                "generation_mode": "planned_off_topic",
                "logical_plan": logical_plan
            }

        try:
            compiled = compile_logical_plan(logical_plan, schema_metadata=schema_metadata)
            if _is_select_sql(compiled.sql):
                return {
                    "question": question,
                    "sql": compiled.sql,
                    "prompt_used": json.dumps(logical_plan, indent=2, sort_keys=True),
                    "model": model,
                    "generation_mode": "deterministic_compiler",
                    "logical_plan": logical_plan,
                    "join_confidence": logical_plan.get("join_confidence"),
                    "join_strategy": logical_plan.get("join_strategy"),
                    "parameterized_sql": compiled.parameterized_sql,
                    "sql_params": list(compiled.params),
                    "compiler_warnings": list(compiled.warnings)
                }
            compiler_error = f"Compiler output was not a SELECT/WITH query: {compiled.sql[:160]}"
        except SQLCompilerError as exc:
            compiler_error = str(exc)

        try:
            planned_result = generate_sql_from_plan(
                question=question,
                logical_plan=logical_plan,
                schema_context=schema_context,
                few_shot_examples=few_shot_examples,
                full_schema_ddl=full_schema_ddl,
                model=model,
                deadline=deadline,
            )

            if _is_select_sql(planned_result.get("sql", "")):
                planned_result["generation_mode"] = "planned_llm_fallback"
                planned_result["compiler_error"] = compiler_error
                planned_result["join_confidence"] = logical_plan.get("join_confidence")
                planned_result["join_strategy"] = logical_plan.get("join_strategy")
                return planned_result

            planner_error = f"Plan-aware SQL output was not a SELECT/WITH query: {planned_result.get('sql', '')[:160]}"
        except Exception as exc:
            planner_error = str(exc)

    except Exception as exc:
        planner_error = str(exc)

    fallback = generate_sql_legacy(
        question=question,
        schema_context=schema_context,
        few_shot_examples=few_shot_examples,
        full_schema_ddl=full_schema_ddl,
        model=model,
        deadline=deadline,
    )
    fallback["generation_mode"] = "legacy_fallback"
    fallback["logical_plan"] = logical_plan
    fallback["compiler_error"] = compiler_error
    fallback["planner_error"] = planner_error
    if logical_plan:
        fallback["join_confidence"] = logical_plan.get("join_confidence")
        fallback["join_strategy"] = logical_plan.get("join_strategy")
    return fallback


# ─────────────────────────────────────────────
# SELF-HEALING RETRY PROMPT
# ─────────────────────────────────────────────
SELF_HEAL_SYSTEM_PROMPT = """You are a PostgreSQL query repair expert.

You will receive:
1. A schema definition
2. A broken SQL query
3. The error message from PostgreSQL

Your task: Fix the SQL query so it runs correctly.

Rules:
- Only use table/column names from the schema
- Output ONLY the corrected SQL, no explanation
- Preserve the original intent of the query
- ENSURE THE OUTPUT IS VALID POSTGRESQL SYNTAX ONLY.
"""

def self_heal_sql(
    broken_sql: str,
    error_msg: str,
    schema_ddl: str,
    model: str = DEFAULT_MODEL,
    timeout_seconds: float = LLM_REPAIR_TIMEOUT_SECONDS,
) -> str:
    heal_prompt = (
        f"=== SCHEMA ===\n{schema_ddl}\n\n"
        f"=== BROKEN SQL ===\n{broken_sql}\n\n"
        f"=== ERROR ===\n{error_msg}\n\n"
        "Fix the SQL and return ONLY the corrected query:"
    )
    payload = {
        "model": model,
        "prompt": heal_prompt,
        "system": SELF_HEAL_SYSTEM_PROMPT,
        "stream": False,
        "keep_alive": -1,
        "options": {"temperature": 0.05, "num_predict": LLM_MAX_REPAIR_TOKENS}
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=max(float(timeout_seconds), 0.1))
    resp.raise_for_status()
    raw = resp.json().get("response", "").strip()
    return clean_sql_output(raw)


def _remaining_timeout(deadline: Optional[float], stage_cap: float) -> float:
    if deadline is None:
        return stage_cap
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("NL2SQL pipeline latency budget exhausted before local LLM fallback.")
    return max(min(stage_cap, remaining), 0.1)
