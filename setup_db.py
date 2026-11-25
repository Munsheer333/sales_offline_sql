import psycopg2
import psycopg2.extras
import pandas as pd
import os
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

def setup_database():
    """Initializes the database schema and loads new CSV data into myoffice_db in lowercase format."""
    try:
        db_url = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        print(f"Connecting to database: {DB_NAME}...")
        
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()

        # 1. Execute schema.sql to drops/creates tables
        print("--- Initializing Schema ---")
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        with open(schema_path, "r") as f:
            cur.execute(f.read())
        print("Schema initialized successfully.")

        # 2. Ingest CSV data
        tables = {
            "m60_level": "M60_LEVEL.csv",
            "mom_level": "MOM_LEVEL.csv",
            "industry_performance": "Industry Performance.csv"
        }

        for table, csv_file in tables.items():
            csv_path = os.path.join(os.path.dirname(__file__), csv_file)
            if not os.path.exists(csv_path):
                print(f"Warning: {csv_file} not found. Skipping.")
                continue

            print(f"--- Loading {csv_file} into table {table} ---")
            
            # Read CSV with pandas to clean and format safely
            df = pd.read_csv(csv_path)
            
            # Clean and lowercase columns
            df.columns = [re.sub(r'[^a-zA-Z0-9_]', '_', c.strip().lower()) for c in df.columns]
            
            # Format date columns to standard YYYY-MM-DD
            for col in df.columns:
                if col.lower() == "day_id" or "date" in col.lower() or "invoice_dt" in col.lower() and df[col].dtype == "object":
                    try:
                        df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime('%Y-%m-%d')
                    except Exception as e:
                        print(f"Could not convert column {col} to date: {e}")
            
            # Replace NaN/NaT with None so they translate to NULL in PostgreSQL
            df = df.where(pd.notnull(df), None)
            
            cols = list(df.columns)
            cols_str = ", ".join(cols)
            placeholders = ", ".join(["%s"] * len(cols))
            
            insert_sql = f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            
            # Convert dataframe rows to list of tuples and replace NaN with None
            records = []
            for row in df.itertuples(index=False):
                cleaned_row = tuple(None if pd.isna(x) else x for x in row)
                records.append(cleaned_row)
            
            # Execute values in batches of 1000 for high speed
            psycopg2.extras.execute_batch(cur, insert_sql, records, page_size=1000)
            
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            print(f"Successfully loaded {count} rows into table {table}.")

        cur.close()
        conn.close()
        print("--- Database Setup Complete ---")

    except Exception as e:
        print(f"Error during database setup: {e}")

if __name__ == "__main__":
    import re
    setup_database()
