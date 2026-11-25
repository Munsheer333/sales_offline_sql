import psycopg2
import json
from config import DB_URL
from benchmark.evaluator import compare_db_results_smart

conn = psycopg2.connect(DB_URL)

with open("benchmark/results/accuracy_report.json", "r") as f:
    data = json.load(f)

for item in data.get("details", []):
    if "Retail LPG in FY 2021-2022" in item["question"]:
        expected = item["sql"]["expected"]
        generated = item["sql"]["generated"]
        
        cur = conn.cursor()
        try:
            cur.execute(expected)
            res1 = cur.fetchall()
            print("Expected query result:", res1)
        except Exception as e:
            print("Expected query failed:", e)
            res1 = None
            
        try:
            cur.execute(generated)
            res2 = cur.fetchall()
            print("Generated query result:", res2)
        except Exception as e:
            print("Generated query failed:", e)
            res2 = None
        cur.close()
        
        print("compare_db_results_smart returns:", compare_db_results_smart(conn, expected, generated))

conn.close()
