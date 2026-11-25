import os
import json
from core.phase6_orchestrator import run_pipeline
from config import DB_URL

def main():
    question = "Show actual sales for product MS in MT for Retail in Oct 2025."
    print("Running pipeline...")
    result = run_pipeline(
        question=question,
        conn_str=DB_URL
    )
    print("\n--- Latency Profiling Result in Return Object ---")
    latency_info = result.get("latency_info")
    print(json.dumps(latency_info, indent=2))
    
    # Assertions
    assert latency_info is not None, "latency_info is missing!"
    for key in ["router_ms", "entity_extraction_ms", "retrieval_ms", "bm25_ms", "pgvector_ms", "reranker_ms", "llm_ms", "validator_ms", "db_ms"]:
        assert key in latency_info, f"Key {key} is missing in latency_info!"
        
    print("\nSUCCESS: All expected latency profiling keys are present and formatted correctly!")

if __name__ == "__main__":
    main()
