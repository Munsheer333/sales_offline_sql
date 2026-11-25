import time
import json
from core.sql_memory import SQLMemory

def run_tests():
    memory = SQLMemory()
    
    # Test case: retrieve matching COMPARE example
    t0 = time.perf_counter()
    res = memory.retrieve(
        query="show comparison of hpcl and bpcl sales in maharashtra",
        intent="COMPARE",
        entities={"company": ["HPCL", "BPCL"], "state": "Maharashtra"}
    )
    dt = time.perf_counter() - t0
    print(f"Retrieve latency: {dt * 1000:.4f} ms")
    assert res is not None, "Expected matching example, got None!"
    assert res["intent"] == "COMPARE", f"Expected COMPARE intent, got {res.get('intent')}!"
    assert "MAHARASHTRA" in res["sql"], "Expected SQL to contain MAHARASHTRA"
    
    print("\n--- Running SQL Memory Benchmark ---")
    total_time = 0.0
    runs = 100
    for _ in range(runs):
        t_start = time.perf_counter()
        memory.retrieve(
            query="show comparison of hpcl and bpcl sales in maharashtra",
            intent="COMPARE",
            entities={"company": ["HPCL", "BPCL"], "state": "Maharashtra"}
        )
        total_time += (time.perf_counter() - t_start)
        
    avg_latency = (total_time / runs) * 1000
    print(f"Average Retrieve Latency (over {runs} runs): {avg_latency:.4f} ms")
    assert avg_latency < 5.0, f"Average latency exceeds 5ms target! ({avg_latency:.4f} ms)"
    print("SUCCESS: SQL Memory retrieves correctly and runs way below the <5ms target!")

if __name__ == "__main__":
    run_tests()
