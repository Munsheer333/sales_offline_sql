import os
import sys

from core.phase6_orchestrator import run_pipeline
from config import DB_URL

q = "Compare product performance between two fiscal years."
res = run_pipeline(question=q, conn_str=DB_URL)

print("SUCCESS:", res.get("success"))
print("MODE:", res.get("generation_mode"))
print("SQL:")
print(res.get("sql"))
print("ROWS COUNT:", res.get("row_count"))
if res.get("rows"):
    print("SAMPLE ROW:", res.get("rows")[0])
