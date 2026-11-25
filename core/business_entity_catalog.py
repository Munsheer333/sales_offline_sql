"""Cached PostgreSQL-backed business entity discovery for deterministic planning."""

import re
import threading
import time
from typing import Any, Dict, List, Optional

import psycopg2
from core.db_pool import pooled_connect
from core.phase3_query_planner import COMPANY_NAMES, PRODUCT_NAMES, SBU_VALUES


CATALOG_TTL_SECONDS = 300
_CACHE: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()

ENTITY_COLUMNS = {
    "company": (("industry_performance", "comname"),),
    "sbu": (
        ("industry_performance", "sbu_name"),
        ("mom_level", "sbu_name"),
        ("m60_level", "sbu_name"),
    ),
    "product": (
        ("industry_performance", "productname"),
        ("mom_level", "productname"),
        ("m60_level", "productname"),
    ),
    "state": (("industry_performance", "statename"),),
    "district": (("industry_performance", "distname"),),
    "zone": (
        ("industry_performance", "zone_name"),
        ("mom_level", "zone_name"),
        ("m60_level", "zone_name"),
    ),
}


def get_business_entity_catalog(
    conn_str: str,
    schema_metadata: Optional[dict] = None,
    ttl_seconds: int = CATALOG_TTL_SECONDS,
) -> Dict[str, List[str]]:
    """Return a small in-process cached catalog sourced from current PostgreSQL data."""
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(conn_str)
        if cached and now - cached["loaded_at"] < ttl_seconds:
            return cached["catalog"]

    catalog = _load_catalog(conn_str, schema_metadata)
    with _LOCK:
        _CACHE[conn_str] = {"loaded_at": now, "catalog": catalog}
    return catalog


def invalidate_business_entity_catalog(conn_str: Optional[str] = None) -> None:
    with _LOCK:
        if conn_str is None:
            _CACHE.clear()
        else:
            _CACHE.pop(conn_str, None)


def analyze_business_entities(
    question: str,
    conn_str: Optional[str] = None,
    schema_metadata: Optional[dict] = None,
    catalog: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """Extract catalog-backed entities and explicit unresolved labels."""
    catalog = catalog if catalog is not None else (
        get_business_entity_catalog(conn_str, schema_metadata) if conn_str else {}
    )
    catalog = _with_curated_entities(catalog)
    q_norm = _normalize(question)
    matches: List[Dict[str, Any]] = []

    for entity_type, values in catalog.items():
        for value in sorted(values, key=lambda item: len(str(item)), reverse=True):
            normalized_value = _normalize(str(value))
            if len(normalized_value) < 2 or not _contains_phrase(q_norm, normalized_value):
                continue
            if entity_type == "sbu" and not _sbu_context(q_norm, normalized_value):
                continue
            if entity_type == "product" and _sbu_context(q_norm, normalized_value) and not _labeled_context(q_norm, normalized_value, "product"):
                continue
            if entity_type in {"district", "zone"} and not _entity_has_context(q_norm, normalized_value, entity_type):
                continue
            matches.append({
                "type": entity_type,
                "value": str(value),
                "column": {
                    "company": "comname",
                    "sbu": "sbu_name",
                    "product": "productname",
                    "state": "statename",
                    "district": "distname",
                    "zone": "zone_name",
                }[entity_type],
                "source": "database_entity_catalog",
                "confidence": 0.98,
            })

    matches = _drop_embedded_matches(q_norm, _prefer_longest_matches(_dedupe_matches(matches)))
    unresolved = []
    for entity_type, raw_value in _explicit_labeled_entities(q_norm):
        if entity_type == "sbu" and raw_value in {"all", "every"}:
            continue
        if not any(
            item["type"] == entity_type
            and (
                _normalize(item["value"]) == _normalize(raw_value)
                or _contains_phrase(_normalize(item["value"]), _normalize(raw_value))
            )
            for item in matches
        ):
            unresolved.append({"type": entity_type, "value": raw_value, "source": "explicit_label"})

    return {
        "matches": matches,
        "unresolved": unresolved,
        "catalog_counts": {key: len(values) for key, values in catalog.items()},
    }


def _load_catalog(conn_str: str, schema_metadata: Optional[dict]) -> Dict[str, List[str]]:
    available = _available_columns(schema_metadata)
    catalog: Dict[str, List[str]] = {entity_type: [] for entity_type in ENTITY_COLUMNS}
    conn = pooled_connect(conn_str)
    try:
        cur = conn.cursor()
        for entity_type, candidates in ENTITY_COLUMNS.items():
            values = set()
            for table, column in candidates:
                if available and (table, column) not in available:
                    continue
                cur.execute(
                    f"SELECT DISTINCT {column} FROM {table} "
                    f"WHERE {column} IS NOT NULL AND BTRIM({column}::text) <> '' LIMIT 5000"
                )
                values.update(str(row[0]).strip() for row in cur.fetchall() if row and row[0] is not None)
            catalog[entity_type] = sorted(values)
        cur.close()
    finally:
        conn.close()
    return catalog


def _with_curated_entities(catalog: Dict[str, List[str]]) -> Dict[str, List[str]]:
    merged = {key: list(values) for key, values in catalog.items()}
    merged["company"] = sorted(set(merged.get("company", [])) | set(COMPANY_NAMES))
    merged["sbu"] = _merge_casefold(SBU_VALUES.values(), merged.get("sbu", []))
    merged["product"] = _merge_casefold(merged.get("product", []), PRODUCT_NAMES)
    return merged


def _available_columns(schema_metadata: Optional[dict]) -> set:
    if not schema_metadata:
        return set()
    return {
        (str(table.get("table_name", "")).lower(), str(column.get("clean_name", "")).lower())
        for table in schema_metadata.get("tables", [])
        for column in table.get("columns", [])
    }


def _explicit_labeled_entities(q_norm: str):
    patterns = {
        "company": r"\b(?:for|in)\s+([a-z0-9&.\-]+)\s+company\b",
        "sbu": r"\b(?:for|in|the)\s+([a-z0-9&.\-]+)\s+sbu\b",
        "product": r"\bproduct\s+([a-z0-9&.\- ]+?)(?=\s+(?:for|in|from|during|between|and|fy|fiscal)\b|$)",
        "state": r"\b(?:state|in)\s+([a-z][a-z ]+?)\s+state\b",
        "district": r"\bdistrict\s+([a-z0-9&.\- ]+?)(?=\s+(?:for|in|from|during|between|and|fy|fiscal)\b|$)",
        "zone": r"\b([a-z0-9&.\-]+)\s+zone\b",
    }
    ignore_words = {
        "the", "a", "an", "any", "some", "of", "all", "every", 
        "state", "district", "zone", "company", "sbu", "product",
        "in", "for", "to", "this", "that", "it", "its"
    }
    for entity_type, pattern in patterns.items():
        for match in re.finditer(pattern, q_norm):
            val = match.group(1).strip(" .,-")
            if not val or val.lower() in ignore_words:
                continue
            yield entity_type, val


def _sbu_context(q_norm: str, value: str) -> bool:
    return bool(
        re.search(rf"\b{re.escape(value)}\b\s+sbu\b", q_norm)
        or re.search(rf"\bsbu\b\s+(?:is\s+)?\b{re.escape(value)}\b", q_norm)
    )


def _labeled_context(q_norm: str, value: str, label: str) -> bool:
    return bool(
        re.search(rf"\b{re.escape(value)}\b\s+{label}\b", q_norm)
        or re.search(rf"\b{label}\b\s+(?:is\s+)?\b{re.escape(value)}\b", q_norm)
    )


def _entity_has_context(q_norm: str, value: str, label: str) -> bool:
    return _contains_phrase(q_norm, value) and (
        value.endswith(f" {label}") or _labeled_context(q_norm, value, label)
    )


def _dedupe_matches(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result = []
    for item in matches:
        key = (item["type"], _normalize(item["value"]))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _prefer_longest_matches(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for item in matches:
        normalized = _normalize(item["value"])
        if any(
            other["type"] == item["type"]
            and len(_normalize(other["value"])) > len(normalized)
            and _contains_phrase(_normalize(other["value"]), normalized)
            for other in matches
        ):
            continue
        result.append(item)
    return result


def _drop_embedded_matches(q_norm: str, matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    zone_values = [_normalize(item["value"]) for item in matches if item["type"] == "zone"]
    result = []
    for item in matches:
        normalized = _normalize(item["value"])
        if (
            item["type"] in {"product", "sbu"}
            and any(len(zone) > len(normalized) and _contains_phrase(zone, normalized) for zone in zone_values)
            and not _labeled_context(q_norm, normalized, item["type"])
        ):
            continue
        result.append(item)
    return result


def _merge_casefold(preferred: Any, fallback: Any) -> List[str]:
    result = []
    seen = set()
    for value in [*preferred, *fallback]:
        normalized = _normalize(str(value))
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(str(value))
    return sorted(result)


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())
