"""
NLPSQL SYSTEM — METADATA DISCOVERY SERVICE
==========================================
Queries PostgreSQL tables dynamically to build a live entity catalog.

Entities discovered:
  companies   → industry_performance.comname
  sbus        → industry_performance.sbu_name + mom_level.sbu_name
  products    → industry_performance.productname + mom_level.productname
  states      → industry_performance.statename
  districts   → industry_performance.distname
  zones       → industry_performance.zone_name + mom_level.zone_name
  regions     → industry_performance.ro + mom_level.region_name
  fiscal_years→ industry_performance.fiscal_year + mom_level.fiscal_year

Output: metadata/entities.json
Source of truth: database (never hardcoded).
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import psycopg2

from config import DB_URL

logger = logging.getLogger(__name__)

# ── Output path ───────────────────────────────────────────────────────────────
_BASE_DIR = Path(__file__).resolve().parent.parent
ENTITIES_PATH = _BASE_DIR / "metadata" / "entities.json"

# ── Discovery queries — one per entity type ───────────────────────────────────
# Each entry: (entity_key, sql, value_column)
_DISCOVERY_QUERIES: List[Dict[str, Any]] = [
    {
        "key": "companies",
        "sql": """
            SELECT DISTINCT comname AS val
            FROM industry_performance
            WHERE comname IS NOT NULL AND comname <> ''
            ORDER BY comname
        """,
        "sources": ["industry_performance.comname"],
    },
    {
        "key": "sbus",
        "sql": """
            SELECT DISTINCT sbu_name AS val FROM industry_performance
            WHERE sbu_name IS NOT NULL AND sbu_name <> '' AND sbu_name <> '0'
              AND sbu_name NOT IN ('Common','Mumbai Ref','Renewable Energy','Visakh Ref')
            UNION
            SELECT DISTINCT sbu_name FROM mom_level
            WHERE sbu_name IS NOT NULL AND sbu_name <> '' AND sbu_name <> '0'
              AND sbu_name NOT IN ('Common','Mumbai Ref','Renewable Energy','Visakh Ref')
            ORDER BY val
        """,
        "sources": ["industry_performance.sbu_name", "mom_level.sbu_name"],
    },
    {
        "key": "products",
        "sql": """
            SELECT DISTINCT productname AS val FROM industry_performance
            WHERE productname IS NOT NULL AND productname <> ''
            UNION
            SELECT DISTINCT productname FROM mom_level
            WHERE productname IS NOT NULL AND productname <> ''
            ORDER BY val
        """,
        "sources": ["industry_performance.productname", "mom_level.productname"],
    },
    {
        "key": "states",
        "sql": """
            SELECT DISTINCT statename AS val
            FROM industry_performance
            WHERE statename IS NOT NULL AND statename <> ''
            ORDER BY statename
        """,
        "sources": ["industry_performance.statename"],
    },
    {
        "key": "districts",
        "sql": """
            SELECT DISTINCT distname AS val
            FROM industry_performance
            WHERE distname IS NOT NULL AND distname <> ''
            ORDER BY distname
        """,
        "sources": ["industry_performance.distname"],
    },
    {
        "key": "zones",
        "sql": """
            SELECT DISTINCT zone_name AS val FROM industry_performance
            WHERE zone_name IS NOT NULL AND zone_name <> ''
            UNION
            SELECT DISTINCT zone_name FROM mom_level
            WHERE zone_name IS NOT NULL AND zone_name <> ''
            ORDER BY val
        """,
        "sources": ["industry_performance.zone_name", "mom_level.zone_name"],
    },
    {
        "key": "regions",
        "sql": """
            SELECT DISTINCT ro AS val FROM industry_performance
            WHERE ro IS NOT NULL AND ro <> ''
            UNION
            SELECT DISTINCT region_name FROM mom_level
            WHERE region_name IS NOT NULL AND region_name <> ''
            ORDER BY val
        """,
        "sources": ["industry_performance.ro", "mom_level.region_name"],
    },
    {
        "key": "fiscal_years",
        "sql": """
            SELECT DISTINCT fiscal_year AS val FROM industry_performance
            WHERE fiscal_year IS NOT NULL AND fiscal_year <> ''
            UNION
            SELECT DISTINCT fiscal_year FROM mom_level
            WHERE fiscal_year IS NOT NULL AND fiscal_year <> ''
            ORDER BY val
        """,
        "sources": ["industry_performance.fiscal_year", "mom_level.fiscal_year"],
    },
]


def _connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(DB_URL)


def _fetch_values(cur, query: str) -> List[str]:
    cur.execute(query)
    return [str(row[0]).strip() for row in cur.fetchall() if row[0] is not None]


def discover_entities(force: bool = False) -> Dict[str, Any]:
    """
    Query every PostgreSQL table and build the entity catalog.

    Args:
        force: If True, always refresh even if entities.json is fresh.

    Returns:
        The full entities dict (also written to metadata/entities.json).
    """
    ENTITIES_PATH.parent.mkdir(parents=True, exist_ok=True)

    entities: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    try:
        conn = _connect()
        cur = conn.cursor()
        logger.info("metadata_discovery: connected to database")

        for spec in _DISCOVERY_QUERIES:
            key = spec["key"]
            try:
                values = _fetch_values(cur, spec["sql"])
                entities[key] = {
                    "values": values,
                    "count": len(values),
                    "sources": spec["sources"],
                }
                logger.info("metadata_discovery: %s → %d values", key, len(values))
            except Exception as exc:
                logger.warning("metadata_discovery: failed to fetch %s: %s", key, exc)
                errors[key] = str(exc)
                entities[key] = {"values": [], "count": 0, "sources": spec["sources"], "error": str(exc)}

        cur.close()
        conn.close()

    except Exception as exc:
        logger.error("metadata_discovery: database connection failed: %s", exc)
        raise

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "postgresql",
        "db_url_host": DB_URL.split("@")[-1] if "@" in DB_URL else "unknown",
        "entities": entities,
        "errors": errors,
    }

    ENTITIES_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    logger.info("metadata_discovery: saved to %s", ENTITIES_PATH)
    return output


def load_entities(auto_refresh: bool = True) -> Dict[str, List[str]]:
    """
    Load entities from in-memory cache.
    """
    from core.metadata_cache import get_cached_entities
    return get_cached_entities()


def get_entity_set(entity_key: str) -> Set[str]:
    """Return a normalised (uppercase) set for fast membership checking."""
    from core.metadata_cache import get_cached_entity_set
    return get_cached_entity_set(entity_key)



# ── CLI entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = discover_entities(force=True)
    for key, meta in result["entities"].items():
        print(f"  {key:15s}: {meta['count']:4d} values")
    if result["errors"]:
        print("Errors:", result["errors"])
        sys.exit(1)
    print(f"\nSaved → {ENTITIES_PATH}")
