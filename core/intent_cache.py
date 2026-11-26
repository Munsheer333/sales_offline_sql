"""
Intent Cache — Semantic-key cache for NL2SQL pipeline.

Cache key is built from (intent, metric, sorted_entities) instead of full text,
so that paraphrases like "ONGC sales in AP" and "Show AP sales for ONGC" hit
the same cache entry.

Storage: In-process LRU dict + PostgreSQL persistent store.
Target hit rate: >60%.
"""

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("nlpsql.intent_cache")


def _normalize_value(v: Any) -> str:
    """Normalize a filter value to a canonical lowercase string."""
    if isinstance(v, list):
        return ",".join(sorted(str(x).strip().lower() for x in v))
    return str(v).strip().lower()


def build_cache_key(
    intent: str,
    metric: str,
    filters: Dict[str, Any],
    dimension: Optional[str] = None,
    comparison: Optional[List[str]] = None,
) -> str:
    """Build a deterministic cache key from intent + metric + dimension + comparison + sorted entities."""
    canonical = {
        "intent": intent.strip().upper(),
        "metric": metric.strip().lower(),
        "dimension": str(dimension or "").strip().lower(),
        "comparison": sorted(str(x).strip().lower() for x in (comparison or [])),
        "filters": {
            k: _normalize_value(v)
            for k, v in sorted(filters.items())
        },
    }
    raw = json.dumps(canonical, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class _LRUCache:
    """Thread-safe in-memory LRU cache with fixed capacity."""

    def __init__(self, capacity: int = 512):
        self._capacity = capacity
        self._store: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                return self._store[key]
        return None

    def put(self, key: str, value: Dict[str, Any]) -> None:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = value
            if len(self._store) > self._capacity:
                self._store.popitem(last=False)

    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class IntentCache:
    """Two-tier intent cache: fast in-memory LRU + persistent PostgreSQL."""

    _stats_lock = threading.Lock()
    _ensured_conns: set = set()

    def __init__(self, capacity: int = 512):
        self._mem = _LRUCache(capacity)
        self._hits = 0
        self._misses = 0

    # ── Public API ──────────────────────────────────────────────────────

    def lookup(
        self,
        intent: str,
        metric: str,
        filters: Dict[str, Any],
        dimension: Optional[str] = None,
        comparison: Optional[List[str]] = None,
        conn_str: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Look up a cached result by intent key.  Returns cached entry or None."""
        key = build_cache_key(intent, metric, filters, dimension=dimension, comparison=comparison)

        # Tier 1: in-memory
        entry = self._mem.get(key)
        if entry:
            self._record_hit()
            logger.debug("intent_cache.memory_hit key=%s", key[:12])
            return entry

        # Tier 2: PostgreSQL
        if conn_str:
            entry = self._pg_get(conn_str, key)
            if entry:
                self._mem.put(key, entry)  # promote to memory
                self._record_hit()
                logger.debug("intent_cache.pg_hit key=%s", key[:12])
                return entry

        self._record_miss()
        return None

    def store(
        self,
        intent: str,
        metric: str,
        filters: Dict[str, Any],
        sql: str,
        result: Dict[str, Any],
        dimension: Optional[str] = None,
        comparison: Optional[List[str]] = None,
        conn_str: Optional[str] = None,
    ) -> str:
        """Store a successful result in both memory and PostgreSQL.
        Returns the cache key."""
        key = build_cache_key(intent, metric, filters, dimension=dimension, comparison=comparison)
        entry = {
            "cache_key": key,
            "intent": intent,
            "metric": metric,
            "filters": filters,
            "sql": sql,
            "columns": result.get("columns", []),
            "row_count": result.get("row_count", 0),
            "stored_at": time.time(),
        }
        self._mem.put(key, entry)

        if conn_str:
            self._pg_put(conn_str, key, entry)

        logger.debug("intent_cache.stored key=%s intent=%s metric=%s", key[:12], intent, metric)
        return key

    def stats(self) -> Dict[str, Any]:
        with self._stats_lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "total": total,
                "hit_rate_pct": round(self._hits / total * 100, 2) if total else 0.0,
                "memory_size": self._mem.size(),
            }

    def reset_stats(self) -> None:
        with self._stats_lock:
            self._hits = 0
            self._misses = 0

    def clear(self, conn_str: Optional[str] = None) -> None:
        self._mem.clear()
        if conn_str:
            self._pg_clear(conn_str)
        self.reset_stats()

    # ── Stats helpers ───────────────────────────────────────────────────

    def _record_hit(self):
        with self._stats_lock:
            self._hits += 1

    def _record_miss(self):
        with self._stats_lock:
            self._misses += 1

    # ── PostgreSQL tier ─────────────────────────────────────────────────

    @staticmethod
    def _ensure_table(conn) -> None:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS nlpsql_intent_cache (
                cache_key   TEXT PRIMARY KEY,
                intent      TEXT NOT NULL,
                metric      TEXT NOT NULL,
                filters     JSONB NOT NULL DEFAULT '{}'::jsonb,
                sql         TEXT NOT NULL,
                columns     JSONB NOT NULL DEFAULT '[]'::jsonb,
                row_count   INTEGER NOT NULL DEFAULT 0,
                hit_count   INTEGER NOT NULL DEFAULT 0,
                created_at  TIMESTAMP DEFAULT NOW(),
                updated_at  TIMESTAMP DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_intent_cache_intent
            ON nlpsql_intent_cache (intent, metric);
        """)
        conn.commit()
        cur.close()

    def _ensure_table_once(self, conn, conn_str: str) -> None:
        if conn_str in self._ensured_conns:
            return
        self._ensure_table(conn)
        self._ensured_conns.add(conn_str)

    def _pg_get(self, conn_str: str, key: str) -> Optional[Dict[str, Any]]:
        try:
            from core.db_pool import pooled_connect
            conn = pooled_connect(conn_str)
            self._ensure_table_once(conn, conn_str)
            cur = conn.cursor()
            cur.execute(
                """SELECT cache_key, intent, metric, filters, sql, columns, row_count
                   FROM nlpsql_intent_cache WHERE cache_key = %s""",
                (key,),
            )
            row = cur.fetchone()
            if row:
                # Bump hit count
                cur.execute(
                    "UPDATE nlpsql_intent_cache SET hit_count = hit_count + 1, updated_at = NOW() WHERE cache_key = %s",
                    (key,),
                )
                conn.commit()
                cur.close()
                conn.close()
                return {
                    "cache_key": row[0],
                    "intent": row[1],
                    "metric": row[2],
                    "filters": row[3] if isinstance(row[3], dict) else json.loads(row[3] or "{}"),
                    "sql": row[4],
                    "columns": row[5] if isinstance(row[5], list) else json.loads(row[5] or "[]"),
                    "row_count": row[6],
                }
            cur.close()
            conn.close()
        except Exception as exc:
            logger.warning("intent_cache.pg_get error: %s", exc)
        return None

    def _pg_put(self, conn_str: str, key: str, entry: Dict[str, Any]) -> None:
        try:
            from core.db_pool import pooled_connect
            conn = pooled_connect(conn_str)
            self._ensure_table_once(conn, conn_str)
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO nlpsql_intent_cache
                       (cache_key, intent, metric, filters, sql, columns, row_count)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (cache_key) DO UPDATE SET
                       sql = EXCLUDED.sql,
                       columns = EXCLUDED.columns,
                       row_count = EXCLUDED.row_count,
                       hit_count = nlpsql_intent_cache.hit_count,
                       updated_at = NOW()
                """,
                (
                    key,
                    entry["intent"],
                    entry["metric"],
                    json.dumps(entry.get("filters", {})),
                    entry["sql"],
                    json.dumps(entry.get("columns", [])),
                    entry.get("row_count", 0),
                ),
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as exc:
            logger.warning("intent_cache.pg_put error: %s", exc)

    def _pg_clear(self, conn_str: str) -> None:
        try:
            from core.db_pool import pooled_connect
            conn = pooled_connect(conn_str)
            self._ensure_table(conn)
            cur = conn.cursor()
            cur.execute("DELETE FROM nlpsql_intent_cache")
            conn.commit()
            cur.close()
            conn.close()
        except Exception as exc:
            logger.warning("intent_cache.pg_clear error: %s", exc)


# ── Module-level singleton ──────────────────────────────────────────────
_GLOBAL_INTENT_CACHE: Optional[IntentCache] = None
_GLOBAL_LOCK = threading.Lock()


def get_intent_cache(capacity: int = 512) -> IntentCache:
    """Return the process-wide IntentCache singleton."""
    global _GLOBAL_INTENT_CACHE
    if _GLOBAL_INTENT_CACHE is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_INTENT_CACHE is None:
                _GLOBAL_INTENT_CACHE = IntentCache(capacity)
    return _GLOBAL_INTENT_CACHE
