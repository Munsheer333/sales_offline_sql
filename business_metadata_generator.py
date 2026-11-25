"""Generate a canonical business metadata layer from PostgreSQL values.

Usage:
    python business_metadata_generator.py
    python business_metadata_generator.py --output business_metadata.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import sql

from postgres_schema_introspector import connection_kwargs


ENTITY_COLUMN_PATTERNS = {
    "companies": (r"^(?:comname|company_name|companyname)$",),
    "sbus": (r"^(?:sbu_name|sbuname|orgsbuname)$",),
    "products": (r"^(?:productname|product_name|productname_org|materialgroupname)$",),
    "regions": (r"^(?:region_name|regionname|orgroname|ro)$",),
    "zones": (r"^(?:zone_name|zonename|orgzonename)$",),
    "districts": (r"^(?:distname|district_name|districtname)$",),
    "fiscal_years": (r"^(?:fiscal_year|fiscalyear)$",),
}


def generate_business_metadata(schema_name: str = "public") -> dict[str, list[dict[str, Any]]]:
    collected: dict[str, dict[str, dict[str, Any]]] = {
        entity_type: {} for entity_type in ENTITY_COLUMN_PATTERNS
    }
    with psycopg2.connect(**connection_kwargs()) as connection:
        with connection.cursor() as cursor:
            for table_name, column_name in _discover_columns(cursor, schema_name):
                entity_type = _classify_column(column_name)
                if entity_type:
                    _collect_column_values(cursor, schema_name, table_name, column_name, entity_type, collected)

    return {
        entity_type: sorted(values.values(), key=lambda item: item["canonical_name"].casefold())
        for entity_type, values in collected.items()
    }


def _discover_columns(cursor, schema_name: str) -> list[tuple[str, str]]:
    cursor.execute(
        """
        SELECT c.table_name, c.column_name
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema
         AND t.table_name = c.table_name
        WHERE c.table_schema = %s
          AND t.table_type = 'BASE TABLE'
        ORDER BY c.table_name, c.ordinal_position
        """,
        (schema_name,),
    )
    return cursor.fetchall()


def _classify_column(column_name: str) -> str | None:
    normalized = column_name.strip().lower()
    for entity_type, patterns in ENTITY_COLUMN_PATTERNS.items():
        if any(re.match(pattern, normalized) for pattern in patterns):
            return entity_type
    return None


def _collect_column_values(
    cursor,
    schema_name: str,
    table_name: str,
    column_name: str,
    entity_type: str,
    collected: dict[str, dict[str, dict[str, Any]]],
) -> None:
    statement = sql.SQL(
        """
        SELECT TRIM({column}::text) AS value, COUNT(*) AS occurrences
        FROM {schema}.{table}
        WHERE {column} IS NOT NULL
          AND TRIM({column}::text) <> ''
        GROUP BY TRIM({column}::text)
        ORDER BY TRIM({column}::text)
        """
    ).format(
        schema=sql.Identifier(schema_name),
        table=sql.Identifier(table_name),
        column=sql.Identifier(column_name),
    )
    cursor.execute(statement)
    for raw_value, occurrences in cursor.fetchall():
        canonical_name = _canonicalize(entity_type, raw_value)
        if not canonical_name:
            continue
        canonical_key = canonical_name.casefold()
        record = collected[entity_type].setdefault(
            canonical_key,
            {
                "canonical_name": canonical_name,
                "aliases": [],
                "sources": [],
                "occurrences": 0,
            },
        )
        if raw_value not in record["aliases"]:
            record["aliases"].append(raw_value)
        source = {"table": table_name, "column": column_name}
        if source not in record["sources"]:
            record["sources"].append(source)
        record["occurrences"] += int(occurrences)


def _canonicalize(entity_type: str, value: Any) -> str:
    normalized = re.sub(r"\s+", " ", str(value).strip())
    if not re.search(r"[A-Za-z0-9]", normalized):
        return ""
    if entity_type == "fiscal_years":
        match = re.search(r"(20\d{2})\s*[-/]\s*(20\d{2})", normalized)
        return f"{match.group(1)}-{match.group(2)}" if match else normalized
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate canonical business entities from PostgreSQL.")
    parser.add_argument("--schema", default="public", help="PostgreSQL schema to scan.")
    parser.add_argument("--output", default="business_metadata.json", help="Output JSON file.")
    args = parser.parse_args()

    metadata = generate_business_metadata(args.schema)
    output = Path(args.output)
    output.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    total = sum(len(values) for values in metadata.values())
    print(f"Generated {total} canonical entities in {output}")
    for entity_type, values in metadata.items():
        print(f"- {entity_type}: {len(values)}")


if __name__ == "__main__":
    main()
