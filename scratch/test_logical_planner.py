import time
import json
from core.logical_planner import LogicalQueryPlanner

def run_tests():
    planner = LogicalQueryPlanner()
    
    test_cases = [
        (
            "Compare ONGC sales in Karnataka and AP for FY 2024-25",
            {
                "intent": "COMPARE",
                "metric": "sales",
                "dimension": "state",
                "filters": {
                    "company": "ONGC",
                    "fiscal_year": "2024-25"
                },
                "comparison": ["Karnataka", "AP"]
            }
        ),
        (
            "Show total volume for HPCL in Karnataka during Oct 2025",
            {
                "intent": "TOTAL_SALES",
                "metric": "sales",
                "dimension": None,
                "filters": {
                    "company": "HPCL",
                    "state": "Karnataka",
                    "month": "October",
                    "fiscal_year": "2025-26"
                },
                "comparison": []
            }
        )
    ]
    
    # Warmup
    for query, _ in test_cases:
        planner.plan(query)
        
    print("--- Running Logical Planner Tests & Measuring Latency ---")
    
    total_time = 0.0
    for query, expected in test_cases:
        t0 = time.perf_counter()
        res = planner.plan(query)
        dt = time.perf_counter() - t0
        total_time += dt
        
        print(f"Query: '{query}'")
        print(f"Result: {json.dumps(res, indent=2)}")
        print(f"Latency: {dt * 1000:.4f} ms")
        
        # Check expected outputs
        assert res["intent"] == expected["intent"], f"Expected intent={expected['intent']}, got {res['intent']}!"
        assert res["metric"] == expected["metric"], f"Expected metric={expected['metric']}, got {res['metric']}!"
        assert res["dimension"] == expected["dimension"], f"Expected dimension={expected['dimension']}, got {res['dimension']}!"
        assert res["filters"] == expected["filters"], f"Expected filters={expected['filters']}, got {res['filters']}!"
        assert res["comparison"] == expected["comparison"], f"Expected comparison={expected['comparison']}, got {res['comparison']}!"
            
    avg_latency_ms = (total_time / len(test_cases)) * 1000
    print(f"\nAverage Latency: {avg_latency_ms:.4f} ms")
    assert avg_latency_ms < 5.0, f"Average latency exceeds 5ms target! ({avg_latency_ms:.4f} ms)"
    print("SUCCESS: Logical query planner is completely correct and runs way below the <5ms latency target!")

if __name__ == "__main__":
    run_tests()
