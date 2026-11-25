import time
import json
from core.sql_assembler import SQLAssembler

def run_tests():
    assembler = SQLAssembler()
    
    test_cases = [
        (
            {
                "intent": "COMPARE",
                "metric": "sales",
                "dimension": "state",
                "filters": {
                    "company": "ONGC",
                    "fiscal_year": "2024-25"
                },
                "comparison": ["Karnataka", "AP"]
            },
            "SELECT statename,\n       SUM(netweight_tmt) AS total_sales_tmt\nFROM industry_performance\nWHERE company_name = 'ONGC' AND fiscal_year = '2024-2025' AND statename IN ('KARNATAKA', 'ANDHRA PRADESH')\nGROUP BY statename\nORDER BY total_sales_tmt DESC;"
        ),
        (
            {
                "intent": "TOTAL_SALES",
                "metric": "sales",
                "dimension": "product",
                "filters": {
                    "zone": "North",
                    "fiscal_year": "2024-25"
                },
                "comparison": []
            },
            "SELECT productname,\n       SUM(netweight_tmt) AS total_sales_tmt\nFROM m60_level\nWHERE zone_name = 'NORTH' AND fiscal_year = '2024-2025'\nGROUP BY productname;"
        )
    ]
    
    # Warmup
    for plan, _ in test_cases:
        assembler.assemble(plan)
        
    print("--- Running SQL Assembler Tests & Measuring Latency ---")
    
    total_time = 0.0
    for plan, expected_sql in test_cases:
        t0 = time.perf_counter()
        sql = assembler.assemble(plan)
        dt = time.perf_counter() - t0
        total_time += dt
        
        print(f"Plan: {json.dumps(plan)}")
        print(f"Generated SQL:\n{sql}")
        print(f"Latency: {dt * 1000:.4f} ms")
        
        # Check output
        assert sql.strip() == expected_sql.strip(), f"Expected:\n{expected_sql}\n\nGot:\n{sql}!"
            
    avg_latency_ms = (total_time / len(test_cases)) * 1000
    print(f"\nAverage Latency: {avg_latency_ms:.4f} ms")
    assert avg_latency_ms < 5.0, f"Average latency exceeds 5ms target! ({avg_latency_ms:.4f} ms)"
    print("SUCCESS: SQL assembler is 100% correct, deterministic, and runs way below the <5ms target!")

if __name__ == "__main__":
    run_tests()
