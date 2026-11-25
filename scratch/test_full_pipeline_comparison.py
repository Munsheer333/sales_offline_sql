import sys
import os
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.phase6_orchestrator import run_pipeline
from config import DB_URL

def test_full_pipeline():
    q = "Compare LUBES and TMT sales."
    for i in range(3):
        t0 = time.perf_counter()
        res = run_pipeline(
            question=q,
            conn_str=DB_URL,
        )
        dt = (time.perf_counter() - t0) * 1000
        print(f"Run {i+1}: Success={res.get('success')}, Mode={res.get('generation_mode')}, Time={dt:.2f}ms")
        if i == 0:
            print(f"SQL:\n{res.get('sql')}\n")

if __name__ == "__main__":
    test_full_pipeline()
