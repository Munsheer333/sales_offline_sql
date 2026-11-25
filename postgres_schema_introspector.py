"""Standalone PostgreSQL schema introspector.

Usage:
    python postgres_schema_introspector.py
    python postgres_schema_introspector.py --output schema_metadata.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg2
from dotenv import load_dotenv


def connection_kwargs() -> dict[str, str]:
    load_dotenv()
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": os.getenv("DB_PORT", "5432"),
        "dbname": os.getenv("DB_NAME", "postgres"),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD", ""),
    }


def inspect_schema(schema_name: str = "public") -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    with psycopg2.connect(**connection_kwargs()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """,
                (schema_name,),
            )
            table_names = [row[0] for row in cursor.fetchall()]

            for table_name in table_names:
                metadata[table_name] = {
                    "columns": _get_columns(cursor, schema_name, table_name),
                    "primary_key": _get_primary_key(cursor, schema_name, table_name),
                    "foreign_keys": _get_foreign_keys(cursor, schema_name, table_name),
                }

    return metadata


def _get_columns(cursor, schema_name: str, table_name: str) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema_name, table_name),
    )
    return [
        {
            "name": column_name,
            "data_type": data_type,
            "nullable": is_nullable == "YES",
            "default": column_default,
        }
        for column_name, data_type, is_nullable, column_default in cursor.fetchall()
    ]


def _get_primary_key(cursor, schema_name: str, table_name: str) -> Any:
    cursor.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = %s
          AND tc.table_name = %s
        ORDER BY kcu.ordinal_position
        """,
        (schema_name, table_name),
    )
    columns = [row[0] for row in cursor.fetchall()]
    if not columns:
        return ""
    return columns[0] if len(columns) == 1 else columns


def _get_foreign_keys(cursor, schema_name: str, table_name: str) -> list[dict[str, str]]:
    cursor.execute(
        """
        SELECT
            tc.constraint_name,
            kcu.column_name,
            ccu.table_schema,
            ccu.table_name,
            ccu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = %s
          AND tc.table_name = %s
        ORDER BY tc.constraint_name, kcu.ordinal_position
        """,
        (schema_name, table_name),
    )
    return [
        {
            "constraint_name": constraint_name,
            "column": column_name,
            "references_schema": references_schema,
            "references_table": references_table,
            "references_column": references_column,
            "relationship": f"{table_name}.{column_name} -> {references_table}.{references_column}",
        }
        for constraint_name, column_name, references_schema, references_table, references_column in cursor.fetchall()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract PostgreSQL schema metadata as JSON.")
    parser.add_argument("--schema", default="public", help="PostgreSQL schema to scan.")
    parser.add_argument("--output", default="schema_metadata.json", help="Output JSON file.")
    args = parser.parse_args()

    metadata = inspect_schema(args.schema)
    output = Path(args.output)
    output.write_text(json.dumps(metadata, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Scanned {len(metadata)} tables and wrote {output}")


if __name__ == "__main__":
    main()
