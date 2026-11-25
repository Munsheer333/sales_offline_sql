import time
import json
from core.entity_resolver import DeterministicEntityResolver

def run_tests():
    resolver = DeterministicEntityResolver()
    
    test_cases = [
        ("AP", {"state": "Andhra Pradesh"}),
        ("actual sales", {"metric": "sales"}),
        ("volume", {"metric": "sales"}),
        ("ONGC AP actual sales", {"company": "ONGC", "state": "Andhra Pradesh", "metric": "sales"}),
        ("Show actual sales for BPCL in UP in Oct 2025", {
            "company": "BPCL",
            "state": "Uttar Pradesh",
            "month": "October",
            "metric": "sales",
            "fiscal_year": "2025-2026"
        }),
        ("hindustan petroleam volume in ka", {
            "company": "HPCL",
            "metric": "sales",
            "state": "Karnataka"
        })
    ]
    
    # Warmup
    for query, _ in test_cases:
        resolver.resolve(query)
        
    print("--- Running Tests & Measuring Latency ---")
    
    total_time = 0.0
    for query, expected in test_cases:
        t0 = time.perf_counter()
        res = resolver.resolve(query)
        dt = time.perf_counter() - t0
        total_time += dt
        
        print(f"Query: '{query}'")
        print(f"Result: {json.dumps(res)}")
        print(f"Latency: {dt * 1000:.4f} ms")
        
        # Check expected keys and values
        for k, v in expected.items():
            assert res.get(k) == v, f"Expected {k}={v}, got {res.get(k)}!"
            
    avg_latency_ms = (total_time / len(test_cases)) * 1000
    print(f"\nAverage Latency: {avg_latency_ms:.4f} ms")
    assert avg_latency_ms < 1.0, f"Average latency exceeds 1ms target! ({avg_latency_ms:.4f} ms)"
    print("SUCCESS: Deterministic entity resolver is completely correct and runs way below the <1ms latency target!")

if __name__ == "__main__":
    run_tests()
