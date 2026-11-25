"""
NLPSQL SYSTEM — METADATA IN-MEMORY CACHE
=========================================
Queries PostgreSQL table structures once on startup and stores distinct entity
values in memory. This eliminates all per-request disk and database overhead
from the entity extraction hot path.
"""

import logging
import psycopg2
from typing import Dict, List, Set, Optional

from config import DB_URL

logger = logging.getLogger(__name__)

# Global in-memory storage
_CACHE: Dict[str, List[str]] = {}
_UPPER_SET_CACHE: Dict[str, Set[str]] = {}
_INITIALIZED = False

# ── SQL Queries to execute on startup ─────────────────────────────────────────
_CACHE_QUERIES = {
    "companies": """
        SELECT DISTINCT comname AS val
        FROM industry_performance
        WHERE comname IS NOT NULL AND comname <> ''
        ORDER BY comname
    """,
    "sbus": """
        SELECT DISTINCT sbu_name AS val FROM industry_performance
        WHERE sbu_name IS NOT NULL AND sbu_name <> '' AND sbu_name <> '0'
          AND sbu_name NOT IN ('Common','Mumbai Ref','Renewable Energy','Visakh Ref')
        UNION
        SELECT DISTINCT sbu_name FROM mom_level
        WHERE sbu_name IS NOT NULL AND sbu_name <> '' AND sbu_name <> '0'
          AND sbu_name NOT IN ('Common','Mumbai Ref','Renewable Energy','Visakh Ref')
        ORDER BY val
    """,
    "products": """
        SELECT DISTINCT productname AS val FROM industry_performance
        WHERE productname IS NOT NULL AND productname <> ''
        UNION
        SELECT DISTINCT productname FROM mom_level
        WHERE productname IS NOT NULL AND productname <> ''
        ORDER BY val
    """,
    "states": """
        SELECT DISTINCT statename AS val
        FROM industry_performance
        WHERE statename IS NOT NULL AND statename <> ''
        ORDER BY statename
    """,
    "districts": """
        SELECT DISTINCT distname AS val
        FROM industry_performance
        WHERE distname IS NOT NULL AND distname <> ''
        ORDER BY distname
    """,
    "zones": """
        SELECT DISTINCT zone_name AS val FROM industry_performance
        WHERE zone_name IS NOT NULL AND zone_name <> ''
        UNION
        SELECT DISTINCT zone_name FROM mom_level
        WHERE zone_name IS NOT NULL AND zone_name <> ''
        ORDER BY val
    """,
    "regions": """
        SELECT DISTINCT ro AS val FROM industry_performance
        WHERE ro IS NOT NULL AND ro <> ''
        UNION
        SELECT DISTINCT region_name FROM mom_level
        WHERE region_name IS NOT NULL AND region_name <> ''
        ORDER BY val
    """,
    "fiscal_years": """
        SELECT DISTINCT fiscal_year AS val FROM industry_performance
        WHERE fiscal_year IS NOT NULL AND fiscal_year <> ''
        UNION
        SELECT DISTINCT fiscal_year FROM mom_level
        WHERE fiscal_year IS NOT NULL AND fiscal_year <> ''
        ORDER BY val
    """
}


def initialize_cache(force: bool = False) -> None:
    """
    Connects to the database and populates the entity cache.
    Must be called on startup.
    """
    global _CACHE, _UPPER_SET_CACHE, _INITIALIZED
    if _INITIALIZED and not force:
        return

    logger.info("Initializing metadata cache from database...")
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        for key, sql in _CACHE_QUERIES.items():
            cur.execute(sql)
            values = [str(row[0]).strip() for row in cur.fetchall() if row[0] is not None]
            _CACHE[key] = values
            _UPPER_SET_CACHE[key] = {v.upper() for v in values}
            logger.info("Cached %d values for key: %s", len(values), key)
        cur.close()
        conn.close()
        _INITIALIZED = True
        logger.info("Metadata cache successfully initialized from DB.")
    except Exception as exc:
        logger.error("Failed to initialize metadata cache from database: %s", exc)
        # Attempt fallback to local file if DB is down or unreachable
        _load_fallback_from_file()


def _load_fallback_from_file() -> None:
    """Fallback to loading from metadata/entities.json if DB connection fails."""
    global _CACHE, _UPPER_SET_CACHE, _INITIALIZED
    import json
    from pathlib import Path
    
    entities_path = Path(__file__).resolve().parent.parent / "metadata" / "entities.json"
    if entities_path.exists():
        try:
            logger.warning("Attempting fallback load from local file: %s", entities_path)
            raw = json.loads(entities_path.read_text(encoding="utf-8"))
            for key, meta in raw.get("entities", {}).items():
                values = meta.get("values", [])
                _CACHE[key] = values
                _UPPER_SET_CACHE[key] = {v.upper() for v in values}
            _INITIALIZED = True
            logger.info("Metadata cache loaded from fallback file.")
        except Exception as e:
            logger.error("Fallback load failed: %s", e)
    else:
        logger.error("No fallback entities.json file found. Cache is empty.")


def get_cached_entities() -> Dict[str, List[str]]:
    """Return the cached entity values dict."""
    if not _INITIALIZED:
        initialize_cache()
    return _CACHE


def get_cached_entity_set(key: str) -> Set[str]:
    """Return a normalized set (uppercase) for fast membership checks."""
    if not _INITIALIZED:
        initialize_cache()
    return _UPPER_SET_CACHE.get(key, set())


# Auto-initialize on module import so it runs once on startup
try:
    initialize_cache()
except Exception:
    pass
