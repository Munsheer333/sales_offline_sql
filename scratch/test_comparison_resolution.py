import sys
import os
import time
import json

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logical_planner import LogicalQueryPlanner
from core.sql_assembler import SQLAssembler

def test_queries():
    planner = LogicalQueryPlanner()
    assembler = SQLAssembler()

    queries = [
        "Compare LUBES and TMT sales.",
        "Compare LUBES vs TMT",
        "Difference between LUBES and TMT sales",
        "Compare HPCL and BPCL sales",
        "Compare Maharashtra and Gujarat sales",
        "What are LUBES sales?",
        "Show total sales of MS in North zone",
    ]

    print("=== Testing Query Planning and Assembly Latency ===\n")
    for q in queries:
        t0 = time.perf_counter()
        plan = planner.plan(q)
        t_plan = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        sql = assembler.assemble(plan, question=q)
        t_assemble = (time.perf_counter() - t1) * 1000
        t_total = t_plan + t_assemble

        print(f"Question: {q}")
        print(f"Plan: {json.dumps(plan)}")
        print(f"SQL:\n{sql}")
        print(f"Latency: Plan={t_plan:.3f}ms, Assemble={t_assemble:.3f}ms, Total={t_total:.3f}ms\n" + "-"*50)

if __name__ == "__main__":
    test_queries()
