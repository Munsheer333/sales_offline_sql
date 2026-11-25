"""
NLPSQL SYSTEM - PHASE 02B
Hybrid Retrieval Engine

Extends pgvector retrieval with PostgreSQL full-text retrieval, score fusion,
reranking, business synonym expansion, and confidence scoring.
"""

import asyncio
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg2
from core.db_pool import pooled_connect
from config import EMBED_MODEL, RERANK_CANDIDATES
from core.phase2_cross_encoder_reranker import adaptive_cross_encoder_rerank, is_cross_encoder_configured
from core.observability import PipelineProfiler


VECTOR_WEIGHT = 0.60
TEXT_WEIGHT = 0.30
RRF_WEIGHT = 0.10
RRF_K = 60.0

SCHEMA_FTS_EXPR = (
    "to_tsvector('english', "
    "coalesce(table_name, '') || ' ' || coalesce(column_name, '') || ' ' || coalesce(description, '')"
    ")"
)

EXAMPLES_FTS_EXPR = (
    "to_tsvector('english', "
    "coalesce(question, '') || ' ' || coalesce(intent, '') || ' ' || coalesce(sql_query, '')"
    ")"
)

BUSINESS_SYNONYMS = {
    "actual sales": ["netweight_tmt", "actual_round", "sales volume"],
    "actual": ["netweight_tmt", "actual_round"],
    "sales": ["netweight_tmt", "total", "sales volume"],
    "target": ["target_qty_kl", "target_qty_tmt", "target quantity"],
    "market share": ["market_share_percentage", "comname", "total market sales", "netweight_tmt"],
    "company": ["comname", "coname", "company_name"],
    "companies": ["comname", "coname", "company_name"],
    "district": ["distname", "dist"],
    "state": ["statename", "statecode"],
    "zone": ["zone_name"],
    "region": ["region_name", "ro"],
    "product": ["productname", "materialgroupname", "prod1"],
    "sbu": ["sbu_name", "strategic business unit"],
    "strategic business unit": ["sbu_name", "sbu"],
    "retail": ["sbu_name", "Retail"],
    "i&c": ["sbu_name", "I&C"],
    "lubes": ["sbu_name", "Lubes"],
    "hsd": ["productname", "HSD"],
    "ms": ["productname", "MS"],
    "fy": ["fiscal_year", "fiscalyear"],
    "fiscal year": ["fiscal_year", "fiscalyear"],
    "current fiscal year": ["fiscal_year", "indian financial year"],
    "month": ["month_name", "yearmonth"],
    "day": ["day_id"],
    "date": ["day_id"],
    "m60": ["m60_level", "target"],
    "mom": ["mom_level", "actual", "day_id"],
    "industry": ["industry_performance", "category", "comname"],
    "ap": ["ANDHRA PRADESH", "statename", "statecode"],
    "mt": ["netweight_tmt", "metric tonnes"],
    "tmt": ["netweight_tmt", "thousand metric tonnes"],
    "kl": ["target_qty_kl", "kilolitres"],
}

# Load custom synonyms from synonyms.json if it exists
_synonyms_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "synonyms.json")
if os.path.exists(_synonyms_path):
    try:
        with open(_synonyms_path, "r", encoding="utf-8") as _f:
            _custom = json.load(_f)
            for _k, _v in _custom.items():
                if _k in BUSINESS_SYNONYMS:
                    for _item in _v:
                        if _item not in BUSINESS_SYNONYMS[_k]:
                            BUSINESS_SYNONYMS[_k].append(_item)
                else:
                    BUSINESS_SYNONYMS[_k] = list(_v)
    except Exception:
        pass



def create_full_text_indexes(cur) -> None:
    """Create migration-safe PostgreSQL full-text indexes for hybrid retrieval."""
    cur.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_schema_fts
        ON nlpsql_schema_embeddings
        USING GIN ({SCHEMA_FTS_EXPR});
    """)
    cur.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_pairs_fts
        ON nl_sql_pairs
        USING GIN ({EXAMPLES_FTS_EXPR});
    """)


def expand_business_synonyms(question: str, max_terms: int = 32) -> str:
    """Expand business language into schema-aware retrieval terms."""
    normalized = _normalize(question)
    terms: List[str] = [question.strip()]
    seen = {_normalize(question)}

    for phrase, synonyms in BUSINESS_SYNONYMS.items():
        if _contains_phrase(normalized, phrase):
            for synonym in synonyms:
                key = _normalize(synonym)
                if key and key not in seen:
                    terms.append(synonym)
                    seen.add(key)
                if len(terms) >= max_terms:
                    return " ".join(terms)

    return " ".join(terms)


def retrieve_hybrid_schema(
    question: str,
    conn_str: str,
    embedding: Sequence[float],
    top_k: int = 8,
) -> List[dict]:
    candidate_limit = _candidate_limit(top_k)
    expanded_query = expand_business_synonyms(question)

    conn = pooled_connect(conn_str)
    try:
        cur = conn.cursor()
        t_pg = time.perf_counter()
        vector_rows = _retrieve_vector_schema(cur, embedding, candidate_limit)
        PipelineProfiler.add_ms("pgvector_ms", time.perf_counter() - t_pg)

        t_bm25 = time.perf_counter()
        text_rows = _retrieve_text_schema(cur, expanded_query, candidate_limit)
        PipelineProfiler.add_ms("bm25_ms", time.perf_counter() - t_bm25)
        cur.close()
    finally:
        conn.close()

    results = fuse_retrieval_results(
        question=question,
        vector_rows=vector_rows,
        text_rows=text_rows,
        top_k=max(top_k, RERANK_CANDIDATES) if is_cross_encoder_configured() else top_k,
        result_type="schema",
        expanded_query=expanded_query,
    )
    t_rerank = time.perf_counter()
    reranked = adaptive_cross_encoder_rerank(question, results, "schema")[:top_k]
    PipelineProfiler.add_ms("reranker_ms", time.perf_counter() - t_rerank)
    return reranked


def retrieve_hybrid_examples(
    question: str,
    conn_str: str,
    embedding: Sequence[float],
    top_k: int = 5,
) -> List[dict]:
    candidate_limit = _candidate_limit(top_k)
    expanded_query = expand_business_synonyms(question)

    conn = pooled_connect(conn_str)
    try:
        cur = conn.cursor()
        t_pg = time.perf_counter()
        vector_rows = _retrieve_vector_examples(cur, embedding, candidate_limit)
        PipelineProfiler.add_ms("pgvector_ms", time.perf_counter() - t_pg)

        t_bm25 = time.perf_counter()
        text_rows = _retrieve_text_examples(cur, expanded_query, candidate_limit)
        PipelineProfiler.add_ms("bm25_ms", time.perf_counter() - t_bm25)
        cur.close()
    finally:
        conn.close()

    results = fuse_retrieval_results(
        question=question,
        vector_rows=vector_rows,
        text_rows=text_rows,
        top_k=max(top_k, RERANK_CANDIDATES) if is_cross_encoder_configured() else top_k,
        result_type="example",
        expanded_query=expanded_query,
    )
    t_rerank = time.perf_counter()
    reranked = adaptive_cross_encoder_rerank(question, results, "example")[:top_k]
    PipelineProfiler.add_ms("reranker_ms", time.perf_counter() - t_rerank)
    return reranked


async def retrieve_hybrid_schema_async(
    question: str,
    conn_str: str,
    embedding: Sequence[float],
    top_k: int = 8,
) -> List[dict]:
    return await asyncio.to_thread(retrieve_hybrid_schema, question, conn_str, embedding, top_k)


async def retrieve_hybrid_examples_async(
    question: str,
    conn_str: str,
    embedding: Sequence[float],
    top_k: int = 5,
) -> List[dict]:
    return await asyncio.to_thread(retrieve_hybrid_examples, question, conn_str, embedding, top_k)


def fuse_retrieval_results(
    question: str,
    vector_rows: Sequence[dict],
    text_rows: Sequence[dict],
    top_k: int,
    result_type: str,
    expanded_query: Optional[str] = None,
) -> List[dict]:
    if result_type not in {"schema", "example"}:
        raise ValueError(f"Unsupported retrieval result type: {result_type}")

    candidates: Dict[Any, dict] = {}
    max_text_score = max([float(row.get("text_score", 0.0) or 0.0) for row in text_rows] or [0.0])

    for rank, row in enumerate(vector_rows, 1):
        key = _candidate_key(row, result_type)
        item = candidates.setdefault(key, _base_candidate(row, result_type))
        item["vector_score"] = _clamp(float(row.get("similarity", 0.0) or 0.0))
        item["similarity"] = item["vector_score"]
        item["vector_rank"] = rank
        item["retrieval_sources"].add("vector")

    for rank, row in enumerate(text_rows, 1):
        key = _candidate_key(row, result_type)
        item = candidates.setdefault(key, _base_candidate(row, result_type))
        raw_score = float(row.get("text_score", 0.0) or 0.0)
        item["raw_text_score"] = raw_score
        item["text_score"] = _normalize_text_score(raw_score, max_text_score)
        item["text_rank"] = rank
        item["retrieval_sources"].add("full_text")

    query_tokens = _tokens(expanded_query or question)
    original_tokens = _tokens(question)
    result_list = []
    for item in candidates.values():
        item["hybrid_score"] = _hybrid_score(item)
        item["rerank_score"] = _rerank_score(item, query_tokens, original_tokens, result_type)
        item["retrieval_confidence"] = 0.0
        result_list.append(item)

    result_list.sort(key=_sort_key, reverse=True)
    _apply_confidence_scores(result_list)

    final = []
    for item in result_list[:top_k]:
        sources = sorted(item.pop("retrieval_sources"))
        item["retrieval_sources"] = sources
        item["retrieval_method"] = "hybrid" if len(sources) > 1 else sources[0]
        final.append(_public_result(item, result_type))
    return final


def vector_schema_results(
    question: str,
    conn_str: str,
    embedding: Sequence[float],
    top_k: int = 8,
    retrieval_method: str = "vector",
) -> List[dict]:
    conn = pooled_connect(conn_str)
    try:
        cur = conn.cursor()
        rows = _retrieve_vector_schema(cur, embedding, top_k)
        cur.close()
    finally:
        conn.close()
    return [_public_vector_result(row, "schema", retrieval_method) for row in rows]


def vector_example_results(
    question: str,
    conn_str: str,
    embedding: Sequence[float],
    top_k: int = 5,
    retrieval_method: str = "vector",
) -> List[dict]:
    conn = pooled_connect(conn_str)
    try:
        cur = conn.cursor()
        rows = _retrieve_vector_examples(cur, embedding, top_k)
        cur.close()
    finally:
        conn.close()
    return [_public_vector_result(row, "example", retrieval_method) for row in rows]


async def vector_schema_results_async(
    question: str,
    conn_str: str,
    embedding: Sequence[float],
    top_k: int = 8,
) -> List[dict]:
    return await asyncio.to_thread(vector_schema_results, question, conn_str, embedding, top_k)


async def vector_example_results_async(
    question: str,
    conn_str: str,
    embedding: Sequence[float],
    top_k: int = 5,
) -> List[dict]:
    return await asyncio.to_thread(vector_example_results, question, conn_str, embedding, top_k)


def _retrieve_vector_schema(cur, embedding: Sequence[float], limit: int) -> List[dict]:
    cur.execute(
        """
        SELECT id, table_name, column_name, description,
               1 - (embedding <=> %s::vector) AS similarity
        FROM nlpsql_schema_embeddings
        WHERE embedding_model = %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (list(embedding), EMBED_MODEL, list(embedding), limit),
    )
    return [
        {
            "id": row[0],
            "table": row[1],
            "column": row[2],
            "description": row[3],
            "similarity": float(row[4]),
        }
        for row in cur.fetchall()
    ]


def _retrieve_text_schema(cur, expanded_query: str, limit: int) -> List[dict]:
    cur.execute(
        f"""
        WITH query AS (SELECT websearch_to_tsquery('english', %s) AS q)
        SELECT id, table_name, column_name, description,
               ts_rank_cd({SCHEMA_FTS_EXPR}, query.q) AS text_score
        FROM nlpsql_schema_embeddings, query
        WHERE embedding_model = %s
          AND {SCHEMA_FTS_EXPR} @@ query.q
        ORDER BY text_score DESC
        LIMIT %s
        """,
        (expanded_query, EMBED_MODEL, limit),
    )
    return [
        {
            "id": row[0],
            "table": row[1],
            "column": row[2],
            "description": row[3],
            "text_score": float(row[4]),
        }
        for row in cur.fetchall()
    ]


def _retrieve_vector_examples(cur, embedding: Sequence[float], limit: int) -> List[dict]:
    cur.execute(
        """
        SELECT id, question, sql_query, intent,
               1 - (question_embedding <=> %s::vector) AS similarity
        FROM nl_sql_pairs
        WHERE embedding_model = %s
        ORDER BY question_embedding <=> %s::vector
        LIMIT %s
        """,
        (list(embedding), EMBED_MODEL, list(embedding), limit),
    )
    return [
        {
            "id": row[0],
            "question": row[1],
            "sql": row[2],
            "complexity": row[3] or "GENERAL",
            "similarity": float(row[4]),
        }
        for row in cur.fetchall()
    ]


def _retrieve_text_examples(cur, expanded_query: str, limit: int) -> List[dict]:
    cur.execute(
        f"""
        WITH query AS (SELECT websearch_to_tsquery('english', %s) AS q)
        SELECT id, question, sql_query, intent,
               ts_rank_cd({EXAMPLES_FTS_EXPR}, query.q) AS text_score
        FROM nl_sql_pairs, query
        WHERE embedding_model = %s
          AND {EXAMPLES_FTS_EXPR} @@ query.q
        ORDER BY text_score DESC
        LIMIT %s
        """,
        (expanded_query, EMBED_MODEL, limit),
    )
    return [
        {
            "id": row[0],
            "question": row[1],
            "sql": row[2],
            "complexity": row[3] or "GENERAL",
            "text_score": float(row[4]),
        }
        for row in cur.fetchall()
    ]


def _candidate_limit(top_k: int) -> int:
    return max(top_k * 4, 20)


def _candidate_key(row: dict, result_type: str) -> Any:
    if row.get("id") is not None:
        return row["id"]
    if result_type == "schema":
        return (row.get("table"), row.get("column"), row.get("description"))
    return (row.get("question"), row.get("sql"))


def _base_candidate(row: dict, result_type: str) -> dict:
    base = {
        "id": row.get("id"),
        "vector_score": 0.0,
        "text_score": 0.0,
        "raw_text_score": 0.0,
        "similarity": 0.0,
        "vector_rank": None,
        "text_rank": None,
        "hybrid_score": 0.0,
        "rerank_score": 0.0,
        "retrieval_sources": set(),
    }
    if result_type == "schema":
        base.update({
            "table": row.get("table"),
            "column": row.get("column"),
            "description": row.get("description", ""),
        })
    else:
        base.update({
            "question": row.get("question", ""),
            "sql": row.get("sql", ""),
            "complexity": row.get("complexity") or "GENERAL",
        })
    return base


def _hybrid_score(item: dict) -> float:
    vector_score = _clamp(item.get("vector_score", 0.0))
    text_score = _clamp(item.get("text_score", 0.0))
    rrf_score = _rrf(item.get("vector_rank"), item.get("text_rank"))
    return _clamp(VECTOR_WEIGHT * vector_score + TEXT_WEIGHT * text_score + RRF_WEIGHT * rrf_score)


def _rrf(vector_rank: Optional[int], text_rank: Optional[int]) -> float:
    score = 0.0
    if vector_rank:
        score += 1.0 / (RRF_K + vector_rank)
    if text_rank:
        score += 1.0 / (RRF_K + text_rank)
    max_score = 2.0 / (RRF_K + 1.0)
    return _clamp(score / max_score)


def _rerank_score(item: dict, expanded_tokens: set, original_tokens: set, result_type: str) -> float:
    if result_type == "schema":
        text = " ".join(str(item.get(key) or "") for key in ["table", "column", "description"])
    else:
        text = " ".join(str(item.get(key) or "") for key in ["question", "complexity", "sql"])

    candidate_tokens = _tokens(text)
    expanded_overlap = _overlap_ratio(expanded_tokens, candidate_tokens)
    original_overlap = _overlap_ratio(original_tokens, candidate_tokens)
    source_bonus = 0.08 if {"vector", "full_text"}.issubset(item.get("retrieval_sources", set())) else 0.0
    exact_bonus = 0.12 if result_type == "example" and _tokens(item.get("question", "")) == original_tokens else 0.0
    lexical_score = _clamp((0.65 * original_overlap) + (0.35 * expanded_overlap) + source_bonus + exact_bonus)
    return _clamp((0.78 * item["hybrid_score"]) + (0.22 * lexical_score))


def _apply_confidence_scores(results: List[dict]) -> None:
    if not results:
        return

    top = results[0]["rerank_score"]
    second = results[1]["rerank_score"] if len(results) > 1 else 0.0
    margin = max(top - second, 0.0)

    for index, item in enumerate(results):
        source_diversity = min(len(item.get("retrieval_sources", [])) / 2.0, 1.0)
        rank_discount = 1.0 / (1.0 + (index * 0.08))
        confidence = (0.62 * item["rerank_score"]) + (0.23 * margin) + (0.15 * source_diversity)
        item["retrieval_confidence"] = round(_clamp(confidence * rank_discount), 4)


def _sort_key(item: dict) -> Tuple[int, float, float, float]:
    exact_vector_priority = 1 if item.get("vector_score", 0.0) >= 0.98 else 0
    return (
        exact_vector_priority,
        item.get("rerank_score", 0.0),
        item.get("hybrid_score", 0.0),
        item.get("vector_score", 0.0),
    )


def _public_result(item: dict, result_type: str) -> dict:
    public = {
        "similarity": round(float(item.get("similarity", 0.0)), 6),
        "vector_score": round(float(item.get("vector_score", 0.0)), 6),
        "text_score": round(float(item.get("text_score", 0.0)), 6),
        "hybrid_score": round(float(item.get("hybrid_score", 0.0)), 6),
        "rerank_score": round(float(item.get("rerank_score", 0.0)), 6),
        "retrieval_confidence": item.get("retrieval_confidence", 0.0),
        "retrieval_method": item.get("retrieval_method", "hybrid"),
        "retrieval_sources": item.get("retrieval_sources", []),
        "cross_encoder_score": item.get("cross_encoder_score"),
        "reranker_applied": item.get("reranker_applied", False),
        "reranker_reason": item.get("reranker_reason"),
    }
    if result_type == "schema":
        public.update({
            "table": item.get("table"),
            "column": item.get("column"),
            "description": item.get("description", ""),
        })
    else:
        public.update({
            "question": item.get("question", ""),
            "sql": item.get("sql", ""),
            "complexity": item.get("complexity") or "GENERAL",
        })
    return public


def _public_vector_result(row: dict, result_type: str, retrieval_method: str) -> dict:
    item = _base_candidate(row, result_type)
    item["similarity"] = _clamp(float(row.get("similarity", 0.0) or 0.0))
    item["vector_score"] = item["similarity"]
    item["hybrid_score"] = item["similarity"]
    item["rerank_score"] = item["similarity"]
    item["retrieval_confidence"] = round(item["similarity"], 4)
    item["retrieval_sources"] = ["vector"]
    item["retrieval_method"] = retrieval_method
    return _public_result(item, result_type)


def _normalize_text_score(score: float, max_score: float) -> float:
    if max_score <= 0:
        return 0.0
    return _clamp(score / max_score)


def _overlap_ratio(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left)


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9_]+", _normalize(text)))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _contains_phrase(normalized_text: str, phrase: str) -> bool:
    phrase_norm = _normalize(phrase)
    if len(phrase_norm) <= 3:
        return bool(re.search(rf"\b{re.escape(phrase_norm)}\b", normalized_text))
    return phrase_norm in normalized_text


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(max(value, low), high)
