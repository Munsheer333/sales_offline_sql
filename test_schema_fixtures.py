"""Business-schema fixtures for planner, validator, and repair tests."""


def relationship_schema():
    return {
        "tables": [
            {
                "table_name": "m60_level",
                "description": "Monthly target and actual sales metrics.",
                "columns": [
                    {"clean_name": "engine_id", "pg_type": "TEXT"},
                    {"clean_name": "orgsbucd", "pg_type": "TEXT"},
                    {"clean_name": "sbu_name", "pg_type": "TEXT"},
                    {"clean_name": "target_qty_tmt", "pg_type": "NUMERIC(18,4)"},
                    {"clean_name": "region_name", "pg_type": "TEXT"},
                ],
            },
            {
                "table_name": "mom_level",
                "description": "Daily sales metrics.",
                "columns": [
                    {"clean_name": "engine_id", "pg_type": "TEXT"},
                    {"clean_name": "orgsbucd", "pg_type": "TEXT"},
                    {"clean_name": "productname", "pg_type": "TEXT"},
                    {"clean_name": "netweight_tmt", "pg_type": "NUMERIC(18,4)"},
                ],
            },
            {
                "table_name": "industry_performance",
                "description": "Company and industry performance metrics.",
                "columns": [
                    {"clean_name": "entity_id", "pg_type": "TEXT"},
                    {"clean_name": "productname", "pg_type": "TEXT"},
                    {"clean_name": "comname", "pg_type": "TEXT"},
                    {"clean_name": "netweight_tmt", "pg_type": "NUMERIC(18,4)"},
                    {"clean_name": "statename", "pg_type": "TEXT"},
                ],
            },
        ],
        "relationships": [
            {
                "from_table": "m60_level",
                "from_column": "orgsbucd",
                "to_table": "mom_level",
                "to_column": "orgsbucd",
                "confidence": 0.97,
            },
            {
                "from_table": "mom_level",
                "from_column": "productname",
                "to_table": "industry_performance",
                "to_column": "productname",
                "confidence": 0.95,
            },
        ],
    }


def validation_schema():
    return relationship_schema()
