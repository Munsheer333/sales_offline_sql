import time
import json
from core.phase6_orchestrator import run_pipeline
from config import DB_URL

def run_tests():
    print("=" * 70)
    print("  CONFIDENCE-BASED ROUTING VERIFICATION")
    print("=" * 70)
    
    results = []
    
    # ─── Case 1: High Confidence (>95%) → Deterministic Compiler ───
    q1 = "Compare ONGC sales in Karnataka and AP for FY 2024-25"
    print(f"\n[Case 1] HIGH CONFIDENCE (>95%): {q1!r}")
    res1 = run_pipeline(question=q1, conn_str=DB_URL)
    lat1 = res1.get("latency_info", {})
    print(f"  Success     : {res1.get('success')}")
    print(f"  Mode        : {res1.get('generation_mode')}")
    print(f"  Route       : {lat1.get('confidence_route')}")
    print(f"  Confidence  : {lat1.get('planner_confidence')}")
    print(f"  LLM ms      : {lat1.get('llm_ms', 0)}")
    print(f"  SQL          : {res1.get('sql', '')[:120]}...")
    assert res1.get("generation_mode") == "deterministic_compiler", f"Expected deterministic_compiler, got {res1.get('generation_mode')}"
    assert lat1.get("llm_ms", 0) == 0
    assert lat1.get("confidence_route") == "high_deterministic"
    results.append(("HIGH", res1))

    # ─── Case 2: High Confidence (>95%) → Top N query ───
    q2 = "Top 5 companies by sales in FY 2024-25"
    print(f"\n[Case 2] HIGH CONFIDENCE (>95%): {q2!r}")
    res2 = run_pipeline(question=q2, conn_str=DB_URL)
    lat2 = res2.get("latency_info", {})
    print(f"  Success     : {res2.get('success')}")
    print(f"  Mode        : {res2.get('generation_mode')}")
    print(f"  Route       : {lat2.get('confidence_route')}")
    print(f"  Confidence  : {lat2.get('planner_confidence')}")
    print(f"  LLM ms      : {lat2.get('llm_ms', 0)}")
    assert lat2.get("llm_ms", 0) == 0
    assert lat2.get("confidence_route") == "high_deterministic"
    results.append(("HIGH", res2))

    # ─── Case 3: Medium Confidence (70%-95%) → SQL Memory ───
    q3 = "What is the total sales for IOCL in 2024-25?"
    print(f"\n[Case 3] MEDIUM CONFIDENCE (70-95%): {q3!r}")
    res3 = run_pipeline(question=q3, conn_str=DB_URL)
    lat3 = res3.get("latency_info", {})
    print(f"  Success     : {res3.get('success')}")
    print(f"  Mode        : {res3.get('generation_mode')}")
    print(f"  Route       : {lat3.get('confidence_route')}")
    print(f"  Confidence  : {lat3.get('planner_confidence')}")
    print(f"  LLM ms      : {lat3.get('llm_ms', 0)}")
    assert lat3.get("llm_ms", 0) == 0
    assert lat3.get("confidence_route") == "medium_memory"
    results.append(("MEDIUM", res3))

    # ─── Case 4: Low Confidence (<70%) → Hybrid + LLM ───
    q4 = "Explain the correlation between weather and demand"
    print(f"\n[Case 4] LOW CONFIDENCE (<70%): {q4!r}")
    res4 = run_pipeline(question=q4, conn_str=DB_URL)
    lat4 = res4.get("latency_info", {})
    print(f"  Success     : {res4.get('success')}")
    print(f"  Mode        : {res4.get('generation_mode')}")
    print(f"  Route       : {lat4.get('confidence_route')}")
    print(f"  Confidence  : {lat4.get('planner_confidence')}")
    print(f"  LLM ms      : {lat4.get('llm_ms', 0)}")
    assert lat4.get("confidence_route") == "low_llm"
    results.append(("LOW", res4))

    # ─── Summary ───
    print("\n" + "=" * 70)
    print("  ROUTING SUMMARY")
    print("=" * 70)
    llm_count = sum(1 for _, r in results if r.get("latency_info", {}).get("llm_ms", 0) > 0)
    total = len(results)
    llm_pct = round(llm_count / total * 100, 1)
    print(f"  Total queries   : {total}")
    print(f"  LLM used        : {llm_count}")
    print(f"  LLM usage %     : {llm_pct}%")
    print(f"  Target (<10%)   : {'✅ MET' if llm_pct < 10 else '⚠️  EXCEEDED (expected for low-confidence)'}")
    
    for label, r in results:
        lat = r.get("latency_info", {})
        print(f"  [{label:6s}] route={lat.get('confidence_route'):20s} conf={lat.get('planner_confidence')} llm_ms={lat.get('llm_ms',0)} mode={r.get('generation_mode')}")

    print("\n✅ SUCCESS: Confidence-based routing verified across all 3 tiers!")

if __name__ == "__main__":
    run_tests()
