"""Test: Intent Cache with semantic key deduplication.

Proves that paraphrased queries with the same intent+metric+entities
hit the same cache entry, and measures the >60% hit rate target.
"""
import json
import time
from core.phase6_orchestrator import run_pipeline
from core.intent_cache import get_intent_cache, build_cache_key
from config import DB_URL


def test_cache_key_stability():
    print("=" * 70)
    print("  TEST 1: Cache Key Stability")
    print("=" * 70)

    key1 = build_cache_key("TOTAL_SALES", "sales", {"company": "ONGC", "state": "Andhra Pradesh"})
    key2 = build_cache_key("TOTAL_SALES", "sales", {"state": "Andhra Pradesh", "company": "ONGC"})
    key3 = build_cache_key("TOTAL_SALES", "sales", {"company": "ongc", "state": "andhra pradesh"})
    key4 = build_cache_key("COMPARE", "sales", {"company": "ONGC", "state": "Andhra Pradesh"})

    print(f"  Key 1 (company first) : {key1}")
    print(f"  Key 2 (state first)   : {key2}")
    print(f"  Key 3 (lowercase)     : {key3}")
    print(f"  Key 4 (diff intent)   : {key4}")

    assert key1 == key2, "Filter order must not change the key!"
    assert key1 == key3, "Case must not change the key!"
    assert key1 != key4, "Different intent must produce different key!"
    print("  ✅ PASSED\n")


def test_paraphrase_cache_hit():
    print("=" * 70)
    print("  TEST 2: Paraphrase Cache Hit")
    print("=" * 70)

    cache = get_intent_cache()
    cache.clear(conn_str=DB_URL)

    q1 = "Total sales for IOCL in FY 2024-25"
    q2 = "Show IOCL total sales for FY 2024-25"

    print(f"  Query 1 (cold): {q1!r}")
    res1 = run_pipeline(question=q1, conn_str=DB_URL)
    lat1 = res1.get("latency_info", {})
    print(f"    Success: {res1.get('success')}  Mode: {res1.get('generation_mode')}  Cache hit: {lat1.get('intent_cache_hit')}")
    assert res1.get("success"), f"Query 1 failed: {res1.get('error')}"

    print(f"  Query 2 (warm): {q2!r}")
    res2 = run_pipeline(question=q2, conn_str=DB_URL)
    lat2 = res2.get("latency_info", {})
    print(f"    Success: {res2.get('success')}  Mode: {res2.get('generation_mode')}  Cache hit: {lat2.get('intent_cache_hit')}")
    assert res2.get("success"), f"Query 2 failed: {res2.get('error')}"
    assert res2.get("generation_mode") == "intent_cache_hit", \
        f"Expected intent_cache_hit, got {res2.get('generation_mode')}"

    stats = cache.stats()
    print(f"  Cache stats: {json.dumps(stats)}")
    assert stats["hits"] >= 1
    print("  ✅ PASSED\n")


def test_hit_rate():
    """Simulate realistic workload: users frequently rephrase the same questions."""
    print("=" * 70)
    print("  TEST 3: Hit Rate > 60%")
    print("=" * 70)

    cache = get_intent_cache()
    cache.clear(conn_str=DB_URL)
    cache.reset_stats()

    # Realistic workload: each group has 1 unique intent, subsequent queries are paraphrases.
    # 5 unique intents × 3 paraphrases each = 15 queries, 10 expected hits = 66%
    queries = [
        # Group 1: IOCL total sales in FY 2024-25
        "Total sales for IOCL in FY 2024-25",
        "Show IOCL total sales for FY 2024-25",
        "What is the total sales of IOCL for fiscal year 2024-25",
        # Group 2: BPCL total sales in FY 2024-25
        "Total sales for BPCL in FY 2024-25",
        "Show BPCL total sales for FY 2024-25",
        "What is the total sales of BPCL for fiscal year 2024-25",
        # Group 3: Compare HPCL and BPCL
        "Compare HPCL and BPCL sales in FY 2024-25",
        "HPCL vs BPCL sales comparison for FY 2024-25",
        "Show comparison of HPCL and BPCL sales in FY 2024-25",
        # Group 4: ONGC total sales in FY 2024-25
        "Total sales for ONGC in FY 2024-25",
        "Show ONGC total sales for FY 2024-25",
        "What is total ONGC sales in FY 2024-25",
        # Group 5: Compare ONGC and IOCL
        "Compare ONGC and IOCL sales in FY 2024-25",
        "ONGC vs IOCL sales comparison for FY 2024-25",
        "Show comparison of ONGC and IOCL sales in FY 2024-25",
    ]

    for i, q in enumerate(queries, 1):
        res = run_pipeline(question=q, conn_str=DB_URL)
        lat = res.get("latency_info", {})
        hit = lat.get("intent_cache_hit", False)
        mode = res.get("generation_mode", "?")
        ok = res.get("success", False)
        print(f"  [{i:2d}] {'HIT ' if hit else 'MISS'} | ok={ok:<5} | mode={mode:25s} | {q}")

    stats = cache.stats()
    print(f"\n  Cache Stats: {json.dumps(stats, indent=2)}")
    hit_rate = stats["hit_rate_pct"]
    print(f"  Hit Rate: {hit_rate}%")
    print(f"  Target (>60%): {'✅ MET' if hit_rate > 60 else '❌ NOT MET'}")
    assert hit_rate > 60, f"Hit rate {hit_rate}% is below 60% target!"
    print("  ✅ PASSED\n")


def test_latency():
    print("=" * 70)
    print("  TEST 4: Cache Latency")
    print("=" * 70)

    cache = get_intent_cache()
    cache.store("TOTAL_SALES", "sales", {"company": "TEST"}, "SELECT 1", {"columns": [], "row_count": 1})

    times = []
    for _ in range(1000):
        t0 = time.perf_counter()
        cache.lookup("TOTAL_SALES", "sales", {"company": "TEST"})
        times.append((time.perf_counter() - t0) * 1000)

    avg_ms = sum(times) / len(times)
    p99_ms = sorted(times)[int(len(times) * 0.99)]
    print(f"  Avg lookup:  {avg_ms:.4f} ms")
    print(f"  P99 lookup:  {p99_ms:.4f} ms")
    assert avg_ms < 1.0, f"Average latency {avg_ms:.4f}ms exceeds 1ms!"
    print("  ✅ PASSED\n")


if __name__ == "__main__":
    test_cache_key_stability()
    test_paraphrase_cache_hit()
    test_hit_rate()
    test_latency()
    print("=" * 70)
    print("  ALL INTENT CACHE TESTS PASSED ✅")
    print("=" * 70)
