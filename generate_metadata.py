import pandas as pd
import re
import os
import json

base_dir = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(base_dir, "data")
os.makedirs(data_dir, exist_ok=True)

csv_files = {
    "m60_level": "M60_LEVEL.csv",
    "mom_level": "MOM_LEVEL.csv",
    "industry_performance": "Industry Performance.csv"
}

custom_descriptions = {
    "industry_performance": {
        "category": "To classify data categories. While querying, exclude the letter 'O' while retaining uncategorized rows (e.g. category != 'O' OR category IS NULL).",
        "sbu_name": "Identifies the Strategic Business Unit name when specified in the user prompt. Values Include: RETAIL, LPG, I&C, AVIATION, PETCHEM, GAS, LUBES.",
        "statename": "Used to filter data by state name (e.g. ASSAM, BIHAR, PUNJAB, etc.) if mentioned in the prompt.",
        "comname": "Extracts company-related data when the company name is present in the user input. Examples of Companies: HPCL, IOCL, BPCL, GAIL, SMA, OIL, etc.",
        "distname": "Filters data by district if the district name (e.g. AGRA, AKOLA, ANANTAPUR, etc.) is mentioned.",
        "zone_name": "Used to filter data based on zone names mentioned in the prompt. Examples of Zones: ECZ, NZ, NCZ, etc.",
        "fiscal_year": "Determines the fiscal year of the data to be displayed. Logic: If the fiscal year is explicitly mentioned in the prompt, use that. If not specified, dynamically fetch the current fiscal year and the previous fiscal year. Avoid hardcoding; calculate based on the current date. Example Format: 2025-2026",
        "month_name": "Retrieves data based on the specified month (e.g., APR, MAR, JUN, JUL, etc.).",
        "netweight_tmt": "Used to calculate total sales values. Logic: By default, display values in TMT (Thousand Metric Tonnes). If MT (Metric Tonnes) is specified in the prompt, convert and display accordingly by multiplying netweight_tmt by 1000.",
        "productname": "Filters data based on specific product names mentioned in the prompt (e.g. MS, HSD, PROPANE, etc.).",
        "ro": "Filters data by region name when specified in the prompt (e.g. NELLORE, MUMBAI, KADAPA, etc.)"
    },
    "mom_level": {
        "sbu_name": "Strategic Business Unit name. When Retail SBU is specified, it should be added in the WHERE clause. Always exclude common or non-operational business units: sbu_name != '0' AND sbu_name NOT IN ('Common', 'Mumbai Ref', 'Renewable Energy', 'Visakh Ref')",
        "productname": "Filters data based on specific product names (e.g. ms, hsd, propane, etc.) when mentioned in prompt.",
        "day_id": "Day or date ID. If a date or period is specified, the query should include the appropriate date range conditions on day_id (e.g. day_id BETWEEN '2025-12-01' AND '2025-12-15').",
        "netweight_tmt": "Used to calculate total sales values in TMT (Thousand Metric Tonnes). If MT (Metric Tonnes) is specified in prompt, convert by multiplying netweight_tmt by 1000."
    }
}

def infer_type(col_name, series):
    if col_name.lower() == "day_id":
        return "DATE"
    dtype = series.dtype
    sample = series.dropna().head(100)
    if dtype == "bool":
        return "BOOLEAN"
    if dtype in ["int32", "int64"]:
        return "BIGINT" if series.max() > 2_147_483_647 else "INTEGER"
    if dtype == "float64":
        return "NUMERIC(18,4)"
    if dtype == "object":
        # Check if Date
        try:
            pd.to_datetime(sample)
            return "DATE"
        except Exception:
            pass
        return "TEXT"
    return "TEXT"

def main():
    analyses = []
    ddl_statements = []

    # Header drop commands
    ddl_statements.append("-- Enable pgvector extension")
    ddl_statements.append("CREATE EXTENSION IF NOT EXISTS vector;")
    ddl_statements.append("")
    ddl_statements.append("-- Drop tables for a clean setup")
    for table in csv_files.keys():
        ddl_statements.append(f"DROP TABLE IF EXISTS {table} CASCADE;")
    ddl_statements.append("DROP TABLE IF EXISTS nl_sql_pairs CASCADE;")
    ddl_statements.append("DROP TABLE IF EXISTS nlpsql_schema_embeddings CASCADE;")
    ddl_statements.append("DROP TABLE IF EXISTS nlpsql_query_history CASCADE;")
    ddl_statements.append("")

    for table_name, csv_filename in csv_files.items():
        csv_path = os.path.join(base_dir, csv_filename)
        if not os.path.exists(csv_path):
            print(f"File not found: {csv_path}")
            continue
        
        print(f"Analyzing {table_name}...")
        full_df = pd.read_csv(csv_path)
        total_rows = len(full_df)
        
        columns_meta = []
        ddl_cols = []
        
        for col in full_df.columns:
            clean_col = re.sub(r'[^a-zA-Z0-9_]', '_', col.strip().lower())
            pg_type = infer_type(clean_col, full_df[col])
            null_count = full_df[col].isnull().sum()
            unique_count = full_df[col].nunique()
            
            # Check custom description
            desc = ""
            if table_name in custom_descriptions and clean_col in custom_descriptions[table_name]:
                desc = custom_descriptions[table_name][clean_col]
            else:
                desc = f"Column containing {clean_col} details."
            
            columns_meta.append({
                "original_name": col,
                "clean_name": clean_col,  # Standard lowercase casing!
                "pg_type": pg_type,
                "null_count": int(null_count),
                "unique_count": int(unique_count),
                "total_rows": int(total_rows),
                "description": desc,
                "is_likely_pk": False
            })
            
            ddl_cols.append(f"    {clean_col} {pg_type}")
        
        # Create Table Statement (Lowercase, no quotes!)
        ddl = f"CREATE TABLE {table_name} (\n" + ",\n".join(ddl_cols) + "\n);"
        ddl_statements.append(ddl)
        ddl_statements.append("")
        
        # Add index
        ddl_statements.append(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_sbu ON {table_name} (sbu_name);")
        ddl_statements.append("")
        
        analyses.append({
            "table_name": table_name,
            "total_rows": total_rows,
            "columns": columns_meta,
            "basic_ddl": ddl,
            "description": f"Table containing {table_name} metrics and business details."
        })

    # Append standard system tables
    ddl_statements.append("""
-- NL-SQL Pairs table for RAG few-shot
CREATE TABLE IF NOT EXISTS nl_sql_pairs (
    id SERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    sql_query TEXT NOT NULL,
    intent VARCHAR(50),
    question_embedding vector(768),
    embedding_model TEXT NOT NULL DEFAULT 'legacy',
    created_at TIMESTAMP DEFAULT NOW()
);

-- Schema Embeddings table
CREATE TABLE IF NOT EXISTS nlpsql_schema_embeddings (
    id SERIAL PRIMARY KEY,
    table_name TEXT NOT NULL,
    column_name TEXT,
    description TEXT NOT NULL,
    embedding vector(768),
    embedding_model TEXT NOT NULL DEFAULT 'legacy',
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Query History Table
CREATE TABLE IF NOT EXISTS nlpsql_query_history (
    id SERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    generated_sql TEXT NOT NULL,
    complexity VARCHAR(50),
    was_valid BOOLEAN,
    execution_time_ms INTEGER,
    row_count INTEGER,
    error_message TEXT,
    feedback INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_schema_embed ON nlpsql_schema_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_pairs_embed ON nl_sql_pairs USING hnsw (question_embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_schema_embedding_model ON nlpsql_schema_embeddings (embedding_model);
CREATE INDEX IF NOT EXISTS idx_pairs_embedding_model ON nl_sql_pairs (embedding_model);
""")

    # Write schema.sql
    schema_path = os.path.join(base_dir, "schema.sql")
    with open(schema_path, "w") as f:
        f.write("\n".join(ddl_statements))
    print(f"Generated schema.sql at {schema_path}")

    # Write schema_metadata.json
    schema_metadata = {"tables": []}
    for a in analyses:
        llm_context_lines = [f"Table: {a['table_name']} ({a['total_rows']:,} rows) - {a['description']}\n"]
        for col in a["columns"]:
            llm_context_lines.append(
                f"  Column: {col['clean_name']} | Type: {col['pg_type']} | "
                f"Description: {col['description']}"
            )
        llm_context = "\n".join(llm_context_lines)
        
        schema_metadata["tables"].append({
            "table_name": a["table_name"],
            "total_rows": a["total_rows"],
            "columns": a["columns"],
            "basic_ddl": a["basic_ddl"],
            "description": a["description"],
            "llm_context": llm_context
        })

    metadata_output_path = os.path.join(data_dir, "schema_metadata.json")
    with open(metadata_output_path, "w") as f:
        json.dump(schema_metadata, f, indent=2)
    print(f"Generated schema_metadata.json at {metadata_output_path}")

if __name__ == "__main__":
    main()
