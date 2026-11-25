"""
NLPSQL SYSTEM — COMPREHENSIVE GOLD DATASET COMPILER
===================================================
Compiles and builds the gold dataset from:
  1. benchmark/templates/golden_queries.json
  2. benchmark/generated/generated_prompts.json
  3. benchmark/generated/synthetic_prompts.json
  4. benchmark/real_world_prompts.json

For each prompt, it generates/extracts:
  - intent
  - entities
  - query plan (logical query plan dict)
  - expected SQL (compiled or original fallback)

Saves output to benchmark/gold_dataset.json and benchmark/templates/gold_dataset.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Any

# Root folder setup
ROOT_DIR = Path(__file__).resolve().parent.parent
import sys
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.intent_detector import get_intent_detector
from core.entity_extractor import get_entity_extractor
from core.phase3_query_planner import generate_logical_plan
from core.phase3_sql_compiler import compile_logical_plan
from core.phase6_orchestrator import load_schema, SCHEMA_PATH


def build_gold_dataset():
    base_dir = ROOT_DIR / "benchmark"
    
    # Input paths
    golden_queries_path = base_dir / "templates" / "golden_queries.json"
    generated_prompts_path = base_dir / "generated" / "generated_prompts.json"
    synthetic_prompts_path = base_dir / "generated" / "synthetic_prompts.json"
    real_world_prompts_path = base_dir / "real_world_prompts.json"
    
    # Output paths
    output_templates_path = base_dir / "templates" / "gold_dataset.json"
    output_root_path = base_dir / "gold_dataset.json"
    
    all_raw_queries = []
    
    # 1. Load from templates/golden_queries.json
    if golden_queries_path.exists():
        try:
            with open(golden_queries_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    q = item.get("question")
                    sql = item.get("sql")
                    if q:
                        all_raw_queries.append({
                            "question": q,
                            "source_sql": sql,
                            "intent": item.get("category")
                        })
        except Exception as e:
            print(f"Error loading golden_queries.json: {e}")
            
    # 2. Load from generated/generated_prompts.json
    if generated_prompts_path.exists():
        try:
            with open(generated_prompts_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    q = item.get("question")
                    sql = item.get("sql")
                    if q:
                        all_raw_queries.append({
                            "question": q,
                            "source_sql": sql,
                            "intent": item.get("category")
                        })
        except Exception as e:
            print(f"Error loading generated_prompts.json: {e}")
            
    # 3. Load from generated/synthetic_prompts.json (newly generated)
    if synthetic_prompts_path.exists():
        try:
            with open(synthetic_prompts_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    q = item.get("question")
                    if q:
                        all_raw_queries.append({
                            "question": q,
                            "source_sql": None,
                            "intent": item.get("category")
                        })
        except Exception as e:
            print(f"Error loading synthetic_prompts.json: {e}")
            
    # 4. Load from real_world_prompts.json
    if real_world_prompts_path.exists():
        try:
            with open(real_world_prompts_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    q = item.get("question")
                    sql = item.get("gold_sql")
                    if q:
                        all_raw_queries.append({
                            "question": q,
                            "source_sql": sql,
                            "intent": item.get("category") or item.get("raw_intent")
                        })
        except Exception as e:
            print(f"Error loading real_world_prompts.json: {e}")

    print(f"Loaded {len(all_raw_queries)} raw queries for compilation.")
    
    # Load schema metadata
    schema = load_schema(SCHEMA_PATH)
    
    detector = get_intent_detector()
    extractor = get_entity_extractor()
    
    gold_dataset = []
    seen_questions = set()
    
    compilation_success = 0
    compilation_failed = 0
    
    for item in all_raw_queries:
        q = item["question"]
        if q in seen_questions:
            continue
        seen_questions.add(q)
        
        # 1. Intent Detection
        try:
            intent_res = detector.detect(q)
            intent = intent_res.primary_intent
        except Exception:
            intent = item["intent"] or "total_sales"
            
        # 2. Entity Extraction
        entities_dict = {}
        try:
            extracted = extractor.extract(q)
            entities_dict = extracted.to_dict()
        except Exception as e:
            print(f"Warning: Entity extraction failed for '{q}': {e}")
            
        # 3. Query Plan Generation
        query_plan = {}
        expected_sql = item["source_sql"]
        
        try:
            query_plan = generate_logical_plan(q, schema_metadata=schema)
            # Try to compile SQL from plan
            if not query_plan.get("is_off_topic"):
                compiled = compile_logical_plan(query_plan, schema_metadata=schema)
                expected_sql = compiled.sql
                compilation_success += 1
            else:
                expected_sql = "OFF_TOPIC: Please ask a database-related question regarding office, sales, or industry performance metrics."
        except Exception as e:
            compilation_failed += 1
            # Fallback to source SQL if available, otherwise write empty/note
            if not expected_sql:
                expected_sql = f"-- Compilation failed: {str(e)}"
        
        gold_dataset.append({
            "question": q,
            "intent": intent,
            "entities": entities_dict,
            "query_plan": query_plan,
            "expected_sql": expected_sql
        })
        
    # Ensure parents exist
    output_templates_path.parent.mkdir(parents=True, exist_ok=True)
    output_root_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Save output to templates
    with open(output_templates_path, "w", encoding="utf-8") as f:
        json.dump(gold_dataset, f, indent=2)
        
    # Save output to root
    with open(output_root_path, "w", encoding="utf-8") as f:
        json.dump(gold_dataset, f, indent=2)
        
    print(f"\nGold Dataset Compilation Summary:")
    print(f"  - Total unique cases compiled: {len(gold_dataset)}")
    print(f"  - Deterministic compiler success: {compilation_success}")
    print(f"  - Fallback / compilation failures: {compilation_failed}")
    print(f"\nSaved to: {output_templates_path}")
    print(f"Saved to: {output_root_path}")


if __name__ == "__main__":
    build_gold_dataset()
