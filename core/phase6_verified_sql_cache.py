"""
Verified SQL reuse for the NL2SQL pipeline.

Generated SQL is recorded as a candidate. Only user-approved or gold SQL is
eligible for automatic reuse. Reused SQL is still validated and executed
against current data by the orchestrator.
"""

import hashlib
import json
import re
import threading
import time
from typing import Any, Dict, Optional, Set, Tuple

import psycopg2
from core.db_pool import pooled_connect

from core.phase6_query_pattern_engine import (
    create_query_pattern_cache_tables,
    normalize_sql_for_fingerprint,
)


VERIFIED_SQL_CACHE_VERSION = "verified-sql-cache-v1"
COMPILER_VERSION = "phase3-sql-compiler-v1"
ONTOLOGY_VERSION = "business-ontology-v1"
VALID_STATUSES = {"candidate", "verified", "rejected", "superseded"}
_ENSURED_CONNS: set = set()
_ENSURE_LOCK = threading.Lock()
_REJECTED_CACHE: Dict[str, Tuple[float, Set[Tuple[str, str]]]] = {}
_VERIFIED_COUNT_CACHE: Dict[str, Tuple[float, int]] = {}
_CACHE_TTL_SECONDS = 2.0


def _invalidate_mem_caches(conn_str: Optional[str] = None) -> None:
    if conn_str is None:
        _REJECTED_CACHE.clear()
        _VERIFIED_COUNT_CACHE.clear()
    else:
        _REJECTED_CACHE.pop(conn_str, None)
        _VERIFIED_COUNT_CACHE.pop(conn_str, None)


def _ensure_tables_once(conn, cur, conn_str: str) -> None:
    if conn_str in _ENSURED_CONNS:
        return
    with _ENSURE_LOCK:
        if conn_str in _ENSURED_CONNS:
            return
        create_verified_sql_cache_tables(cur)
        conn.commit()
        _ENSURED_CONNS.add(conn_str)


def normalize_question(question: str) -> str:
    """Normalize harmless surface differences without guessing semantic equivalence."""
    normalized = re.sub(r"[^a-z0-9&%_-]+", " ", (question or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"^(?:please\s+)?(?:what is|show me|give me|get)\s+", "", normalized)
    normalized = re.sub(r"\bthe\b", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def question_fingerprint(question: str) -> str:
    return _hash_text(f"question:{normalize_question(question)}")


def schema_fingerprint(schema_metadata: Optional[dict]) -> str:
    payload = json.dumps(schema_metadata or {}, sort_keys=True, separators=(",", ":"), default=str)
    return _hash_text(f"schema:{payload}")


def sql_fingerprint(sql: str) -> str:
    normalized = normalize_sql_for_fingerprint(sql, include_literals=True)
    return _hash_text(f"sql:{normalized}")


def create_verified_sql_cache_tables(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS nlpsql_verified_sql_cache (
            id SERIAL PRIMARY KEY,
            question_fingerprint TEXT NOT NULL,
            normalized_question TEXT NOT NULL,
            question TEXT NOT NULL,
            sql TEXT NOT NULL,
            sql_fingerprint TEXT NOT NULL,
            schema_fingerprint TEXT NOT NULL,
            cache_version TEXT NOT NULL,
            compiler_version TEXT NOT NULL,
            ontology_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'candidate',
            source_query_id INTEGER,
            rejection_reason TEXT,
            verified_at TIMESTAMP,
            rejected_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            hit_count INTEGER NOT NULL DEFAULT 0,
            UNIQUE (
                question_fingerprint,
                schema_fingerprint,
                cache_version,
                compiler_version,
                ontology_version,
                sql_fingerprint
            )
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_nlpsql_verified_sql_cache_lookup
        ON nlpsql_verified_sql_cache (
            question_fingerprint,
            schema_fingerprint,
            cache_version,
            compiler_version,
            ontology_version,
            status,
            updated_at DESC
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_nlpsql_verified_sql_cache_source
        ON nlpsql_verified_sql_cache (source_query_id);
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS nlpsql_rejected_sql (
            question_fingerprint TEXT NOT NULL,
            normalized_question TEXT NOT NULL,
            question TEXT NOT NULL,
            sql_fingerprint TEXT NOT NULL,
            sql TEXT NOT NULL,
            source_query_id INTEGER,
            rejection_reason TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (question_fingerprint, sql_fingerprint)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_nlpsql_rejected_sql_question
        ON nlpsql_rejected_sql (question_fingerprint, updated_at DESC);
    """)


def ensure_verified_sql_cache(conn_str: str) -> bool:
    try:
        with pooled_connect(conn_str) as conn:
            with conn.cursor() as cur:
                _ensure_tables_once(conn, cur, conn_str)
            conn.commit()
        if status == "verified":
            _invalidate_mem_caches(conn_str)
        return True
    except Exception:
        return False


def backfill_rejected_feedback(conn_str: str) -> int:
    """Create tombstones for historical thumbs-down feedback after migration."""
    try:
        with pooled_connect(conn_str) as conn:
            with conn.cursor() as cur:
                _ensure_tables_once(conn, cur, conn_str)
                cur.execute(
                    """
                    SELECT id, question, generated_sql
                    FROM nlpsql_query_history
                    WHERE feedback = -1
                    """
                )
                inserted = 0
                for query_id, question, sql in cur.fetchall():
                    cur.execute(
                        """
                        INSERT INTO nlpsql_rejected_sql (
                            question_fingerprint, normalized_question, question, sql_fingerprint,
                            sql, source_query_id, rejection_reason, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, 'historical_user_marked_incorrect', NOW())
                        ON CONFLICT (question_fingerprint, sql_fingerprint) DO NOTHING
                        """,
                        (
                            question_fingerprint(question),
                            normalize_question(question),
                            question,
                            sql_fingerprint(sql),
                            sql,
                            query_id,
                        ),
                    )
                    inserted += cur.rowcount
            conn.commit()
        return inserted
    except Exception:
        return 0


def get_verified_sql(conn_str: str, question: str, schema_metadata: dict) -> Optional[dict]:
    """Return approved SQL only. Candidates and rejected entries are never reused."""
    try:
        now = time.monotonic()
        cached_cnt = _VERIFIED_COUNT_CACHE.get(conn_str)
        if cached_cnt and (now - cached_cnt[0]) < _CACHE_TTL_SECONDS and cached_cnt[1] == 0:
            return None
        with pooled_connect(conn_str) as conn:
            with conn.cursor() as cur:
                _ensure_tables_once(conn, cur, conn_str)
                q_fp = question_fingerprint(question)
                s_fp = schema_fingerprint(schema_metadata)
                cur.execute(
                    """
                    SELECT id, sql, sql_fingerprint, hit_count, source_query_id
                    FROM nlpsql_verified_sql_cache
                    WHERE question_fingerprint = %s
                      AND schema_fingerprint = %s
                      AND cache_version = %s
                      AND compiler_version = %s
                      AND ontology_version = %s
                      AND status = 'verified'
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                    """,
                    (q_fp, s_fp, VERIFIED_SQL_CACHE_VERSION, COMPILER_VERSION, ONTOLOGY_VERSION),
                )
                row = cur.fetchone()
                if not row:
                    cur.execute("SELECT 1 FROM nlpsql_verified_sql_cache WHERE status = 'verified' LIMIT 1")
                    _VERIFIED_COUNT_CACHE[conn_str] = (now, 1 if cur.fetchone() else 0)
                    return None
                _VERIFIED_COUNT_CACHE[conn_str] = (now, 1)
                cur.execute(
                    """
                    UPDATE nlpsql_verified_sql_cache
                    SET hit_count = hit_count + 1, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (row[0],),
                )
            conn.commit()
        return {
            "id": row[0],
            "sql": row[1],
            "sql_fingerprint": row[2],
            "hit_count": row[3] + 1,
            "source_query_id": row[4],
        }
    except Exception:
        return None


def store_candidate(
    conn_str: str,
    question: str,
    sql: str,
    schema_metadata: dict,
    source_query_id: Optional[int] = None,
) -> bool:
    return _store_sql(
        conn_str=conn_str,
        question=question,
        sql=sql,
        schema_metadata=schema_metadata,
        source_query_id=source_query_id,
        status="candidate",
    )


def store_verified_sql(
    conn_str: str,
    question: str,
    sql: str,
    schema_metadata: dict,
    source_query_id: Optional[int] = None,
) -> bool:
    """Store an explicitly approved correction and supersede prior approved SQL."""
    return _store_sql(
        conn_str=conn_str,
        question=question,
        sql=sql,
        schema_metadata=schema_metadata,
        source_query_id=source_query_id,
        status="verified",
    )


def is_rejected_sql(conn_str: str, question: str, sql: str) -> bool:
    """Return True when a user previously rejected the same SQL for this question."""
    if not question or not sql:
        return False
    try:
        now = time.monotonic()
        cached = _REJECTED_CACHE.get(conn_str)
        if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
            if not cached[1]:
                return False
            return (question_fingerprint(question), sql_fingerprint(sql)) in cached[1]
        with pooled_connect(conn_str) as conn:
            with conn.cursor() as cur:
                _ensure_tables_once(conn, cur, conn_str)
                cur.execute("SELECT question_fingerprint, sql_fingerprint FROM nlpsql_rejected_sql LIMIT 2000")
                pairs = {(r[0], r[1]) for r in cur.fetchall()}
                _REJECTED_CACHE[conn_str] = (now, pairs)
            conn.commit()
        if not pairs:
            return False
        return (question_fingerprint(question), sql_fingerprint(sql)) in pairs
    except Exception:
        return False


def reject_sql_for_question(conn_str: str, question: str, sql: str, reason: str) -> bool:
    """Block a known semantically incorrect SQL candidate without requiring a history row."""
    if not question or not sql:
        return False
    try:
        with pooled_connect(conn_str) as conn:
            with conn.cursor() as cur:
                _ensure_tables_once(conn, cur, conn_str)
                q_fp = question_fingerprint(question)
                sql_fp = sql_fingerprint(sql)
                cur.execute(
                    """
                    UPDATE nlpsql_verified_sql_cache
                    SET status = 'rejected', rejection_reason = %s, rejected_at = NOW(), updated_at = NOW()
                    WHERE question_fingerprint = %s
                      AND sql_fingerprint = %s
                    """,
                    (reason, q_fp, sql_fp),
                )
                cur.execute(
                    """
                    INSERT INTO nlpsql_rejected_sql (
                        question_fingerprint, normalized_question, question, sql_fingerprint,
                        sql, rejection_reason, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (question_fingerprint, sql_fingerprint) DO UPDATE SET
                        rejection_reason = EXCLUDED.rejection_reason,
                        updated_at = NOW()
                    """,
                    (q_fp, normalize_question(question), question, sql_fp, sql, reason),
                )
            conn.commit()
        _invalidate_mem_caches(conn_str)
        return True
    except Exception:
        return False


def reject_cache_entry(conn_str: str, cache_id: int, reason: str) -> bool:
    try:
        with pooled_connect(conn_str) as conn:
            with conn.cursor() as cur:
                _ensure_tables_once(conn, cur, conn_str)
                cur.execute(
                    """
                    UPDATE nlpsql_verified_sql_cache
                    SET status = 'rejected', rejection_reason = %s, rejected_at = NOW(), updated_at = NOW()
                    WHERE id = %s
                    """,
                    (reason, cache_id),
                )
            conn.commit()
        _invalidate_mem_caches(conn_str)
        return True
    except Exception:
        return False


def record_query_feedback(conn_str: str, query_id: int, feedback: int) -> bool:
    """Promote approved SQL or reject it and block matching pattern-cache entries."""
    try:
        with pooled_connect(conn_str) as conn:
            with conn.cursor() as cur:
                _ensure_tables_once(conn, cur, conn_str)
                create_query_pattern_cache_tables(cur)
                cur.execute(
                    "SELECT question, generated_sql FROM nlpsql_query_history WHERE id = %s",
                    (query_id,),
                )
                row = cur.fetchone()
                if not row:
                    return False

                question, sql = row
                q_fp = question_fingerprint(question)
                sql_fp = sql_fingerprint(sql)
                if feedback > 0:
                    cur.execute(
                        """
                        UPDATE nlpsql_verified_sql_cache
                        SET status = 'superseded', updated_at = NOW()
                        WHERE question_fingerprint = %s
                          AND status = 'verified'
                          AND sql_fingerprint != %s
                        """,
                        (q_fp, sql_fp),
                    )
                    cur.execute(
                        """
                        UPDATE nlpsql_verified_sql_cache
                        SET status = 'verified', verified_at = NOW(), rejected_at = NULL,
                            rejection_reason = NULL, updated_at = NOW()
                        WHERE (source_query_id = %s OR question_fingerprint = %s)
                          AND sql_fingerprint = %s
                        """,
                        (query_id, q_fp, sql_fp),
                    )
                    cur.execute(
                        """
                        UPDATE nlpsql_query_pattern_cache
                        SET verification_status = 'verified', updated_at = NOW()
                        WHERE LOWER(TRIM(question)) = LOWER(TRIM(%s))
                          AND sql = %s
                        """,
                        (question, sql),
                    )
                    cur.execute(
                        """
                        DELETE FROM nlpsql_rejected_sql
                        WHERE question_fingerprint = %s
                          AND sql_fingerprint = %s
                        """,
                        (q_fp, sql_fp),
                    )
                elif feedback < 0:
                    cur.execute(
                        """
                        UPDATE nlpsql_verified_sql_cache
                        SET status = 'rejected', rejection_reason = 'user_marked_incorrect',
                            rejected_at = NOW(), updated_at = NOW()
                        WHERE source_query_id = %s
                           OR (question_fingerprint = %s AND sql_fingerprint = %s)
                        """,
                        (query_id, q_fp, sql_fp),
                    )
                    cur.execute(
                        """
                        UPDATE nlpsql_query_pattern_cache
                        SET verification_status = 'rejected', updated_at = NOW()
                        WHERE LOWER(TRIM(question)) = LOWER(TRIM(%s))
                           OR sql = %s
                        """,
                        (question, sql),
                    )
                    cur.execute(
                        """
                        INSERT INTO nlpsql_rejected_sql (
                            question_fingerprint, normalized_question, question, sql_fingerprint,
                            sql, source_query_id, rejection_reason, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, 'user_marked_incorrect', NOW())
                        ON CONFLICT (question_fingerprint, sql_fingerprint) DO UPDATE SET
                            question = EXCLUDED.question,
                            sql = EXCLUDED.sql,
                            source_query_id = EXCLUDED.source_query_id,
                            rejection_reason = EXCLUDED.rejection_reason,
                            updated_at = NOW()
                        """,
                        (q_fp, normalize_question(question), question, sql_fp, sql, query_id),
                    )
            conn.commit()
        _invalidate_mem_caches(conn_str)
        return True
    except Exception:
        return False


def _store_sql(
    conn_str: str,
    question: str,
    sql: str,
    schema_metadata: dict,
    source_query_id: Optional[int],
    status: str,
) -> bool:
    if status not in VALID_STATUSES or not question or not sql:
        return False
    try:
        with pooled_connect(conn_str) as conn:
            with conn.cursor() as cur:
                _ensure_tables_once(conn, cur, conn_str)
                q_fp = question_fingerprint(question)
                s_fp = schema_fingerprint(schema_metadata)
                sql_fp = sql_fingerprint(sql)
                if status == "verified":
                    cur.execute(
                        """
                        UPDATE nlpsql_verified_sql_cache
                        SET status = 'superseded', updated_at = NOW()
                        WHERE question_fingerprint = %s
                          AND schema_fingerprint = %s
                          AND status = 'verified'
                          AND sql_fingerprint != %s
                        """,
                        (q_fp, s_fp, sql_fp),
                    )
                cur.execute(
                    """
                    INSERT INTO nlpsql_verified_sql_cache (
                        question_fingerprint, normalized_question, question, sql, sql_fingerprint,
                        schema_fingerprint, cache_version, compiler_version, ontology_version,
                        status, source_query_id, verified_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            CASE WHEN %s = 'verified' THEN NOW() ELSE NULL END, NOW())
                    ON CONFLICT (
                        question_fingerprint, schema_fingerprint, cache_version,
                        compiler_version, ontology_version, sql_fingerprint
                    ) DO UPDATE SET
                        question = EXCLUDED.question,
                        sql = EXCLUDED.sql,
                        source_query_id = COALESCE(EXCLUDED.source_query_id, nlpsql_verified_sql_cache.source_query_id),
                        status = CASE
                            WHEN EXCLUDED.status = 'verified' THEN 'verified'
                            WHEN nlpsql_verified_sql_cache.status = 'verified' THEN 'verified'
                            WHEN nlpsql_verified_sql_cache.status = 'rejected' THEN 'rejected'
                            ELSE EXCLUDED.status
                        END,
                        verified_at = CASE
                            WHEN EXCLUDED.status = 'verified' THEN NOW()
                            ELSE nlpsql_verified_sql_cache.verified_at
                        END,
                        updated_at = NOW()
                    """,
                    (
                        q_fp,
                        normalize_question(question),
                        question,
                        sql,
                        sql_fp,
                        s_fp,
                        VERIFIED_SQL_CACHE_VERSION,
                        COMPILER_VERSION,
                        ONTOLOGY_VERSION,
                        status,
                        source_query_id,
                        status,
                    ),
                )
            conn.commit()
        if status == "verified":
            _invalidate_mem_caches(conn_str)
        return True
    except Exception:
        return False


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
