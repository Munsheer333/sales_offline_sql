"""
NLPSQL SYSTEM — COMPREHENSIVE BENCHMARK IMPORT & LIBRARY CREATOR
==================================================================
Imports natural language prompts from all benchmark sources:
  1. data/gold_tests.json
  2. data/benchmark_total_sales_prompts.csv
  3. benchmark/gold_dataset.json
  4. benchmark/edge_cases.json
  5. benchmark/paraphrases.json
  6. benchmark/synonym_variants.json

Deduplicates questions and classifies them into 6 core business intents:
  - sales
  - market_share
  - growth
  - ranking
  - comparison
  - trend

Stores flat library at real_world_prompts.json and categorized library at benchmark_library.json.
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Any, Set

# Add root folder to sys.path so we can import from core
import sys
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.intent_detector import get_intent_detector
from core.entity_extractor import get_entity_extractor

# ── Category Map ──────────────────────────────────────────────────────────────
_INTENT_CATEGORY_MAP = {
    "total_sales": "sales",
    "actual_volume": "sales",
    "geography": "sales",
    "market_share": "market_share",
    "growth": "growth",
    "top_n": "ranking",
    "bottom_n": "ranking",
    "comparison": "comparison",
    "sales_difference": "comparison",
    "trend": "trend"
}


def normalize_text(text: str) -> str:
    text = str(text or "").lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text)


def mask_entities(text: str) -> str:
    """Masks business entities using the deterministic entity extractor."""
    try:
        extractor = get_entity_extractor()
        res = extractor.extract(text)
        
        padded = f" {text} "
        
        # 1. Mask Years
        for yr in res.years:
            padded = re.sub(rf"\b{re.escape(yr)}\b", "[YEAR]", padded, flags=re.IGNORECASE)
            
        # 2. Mask Zones
        for zone in res.zones:
            padded = re.sub(rf"\b{re.escape(zone)}\b", "[ZONE]", padded, flags=re.IGNORECASE)
            
        # 3. Mask Regions
        for reg in res.regions:
            padded = re.sub(rf"\b{re.escape(reg)}\b", "[REGION]", padded, flags=re.IGNORECASE)
            
        # 4. Mask SBUs
        for sbu in res.sbus:
            padded = re.sub(rf"\b{re.escape(sbu)}\b", "[SBU]", padded, flags=re.IGNORECASE)
            
        # 5. Mask Companies
        for co in res.companies:
            padded = re.sub(rf"\b{re.escape(co)}\b", "[COMPANY]", padded, flags=re.IGNORECASE)
            
        # 6. Mask Products
        for prod in res.products:
            padded = re.sub(rf"\b{re.escape(prod)}\b", "[PRODUCT]", padded, flags=re.IGNORECASE)
            
        # 7. Mask States
        for state in res.states:
            padded = re.sub(rf"\b{re.escape(state)}\b", "[STATE]", padded, flags=re.IGNORECASE)
            
        # 8. Mask Districts
        for dist in res.districts:
            padded = re.sub(rf"\b{re.escape(dist)}\b", "[DISTRICT]", padded, flags=re.IGNORECASE)

        return padded.strip()
    except Exception:
        # Fallback to simple regex if extractor fails or is missing context
        return text


def import_and_process():
    # Source paths
    gold_tests_path = ROOT_DIR / "data" / "gold_tests.json"
    total_sales_path = ROOT_DIR / "data" / "benchmark_total_sales_prompts.csv"
    gold_dataset_path = ROOT_DIR / "benchmark" / "gold_dataset.json"
    edge_cases_path = ROOT_DIR / "benchmark" / "edge_cases.json"
    paraphrases_path = ROOT_DIR / "benchmark" / "paraphrases.json"
    synonyms_path = ROOT_DIR / "benchmark" / "synonym_variants.json"
    
    # Destination paths
    output_flat = ROOT_DIR / "benchmark" / "real_world_prompts.json"
    output_library = ROOT_DIR / "benchmark" / "benchmark_library.json"
    
    raw_prompts = []
    
    # 1. Load data/gold_tests.json
    if gold_tests_path.exists():
        try:
            with open(gold_tests_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    q = item.get("question") or item.get("prompt")
                    if q:
                        raw_prompts.append({
                            "question": q,
                            "source": "gold_tests.json",
                            "gold_sql": item.get("gold_sql")
                        })
        except Exception as e:
            print(f"Error loading gold_tests.json: {e}")
            
    # 2. Load data/benchmark_total_sales_prompts.csv
    if total_sales_path.exists():
        try:
            with open(total_sales_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    q = row.get("prompt") or row.get("question")
                    if q:
                        raw_prompts.append({
                            "question": q,
                            "source": "benchmark_total_sales_prompts.csv",
                            "gold_sql": None
                        })
        except Exception as e:
            print(f"Error loading benchmark_total_sales_prompts.csv: {e}")
            
    # 3. Load benchmark/gold_dataset.json
    if gold_dataset_path.exists():
        try:
            with open(gold_dataset_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    q = item.get("question")
                    if q:
                        raw_prompts.append({
                            "question": q,
                            "source": "gold_dataset.json",
                            "gold_sql": item.get("expected_sql")
                        })
        except Exception as e:
            print(f"Error loading gold_dataset.json: {e}")

    # 4. Load benchmark/edge_cases.json
    if edge_cases_path.exists():
        try:
            with open(edge_cases_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    q = item.get("question")
                    if q:
                        raw_prompts.append({
                            "question": q,
                            "source": "edge_cases.json",
                            "gold_sql": None
                        })
        except Exception as e:
            print(f"Error loading edge_cases.json: {e}")

    # 5. Load benchmark/paraphrases.json
    if paraphrases_path.exists():
        try:
            with open(paraphrases_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    q = item.get("question")
                    if q:
                        raw_prompts.append({
                            "question": q,
                            "source": "paraphrases.json",
                            "gold_sql": item.get("sql")
                        })
        except Exception as e:
            print(f"Error loading paraphrases.json: {e}")

    # 6. Load benchmark/synonym_variants.json
    if synonyms_path.exists():
        try:
            with open(synonyms_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    q = item.get("question")
                    if q:
                        raw_prompts.append({
                            "question": q,
                            "source": "synonym_variants.json",
                            "gold_sql": item.get("sql")
                        })
        except Exception as e:
            print(f"Error loading synonym_variants.json: {e}")

    print(f"Loaded {len(raw_prompts)} raw prompts from all sources.")
    
    detector = get_intent_detector()
    
    flat_results = []
    library: Dict[str, List[Dict[str, Any]]] = {
        "sales": [],
        "market_share": [],
        "growth": [],
        "ranking": [],
        "comparison": [],
        "trend": []
    }
    
    seen_normalized = set()
    
    for item in raw_prompts:
        question = item["question"]
        norm = normalize_text(question)
        
        # Deduplicate
        if norm in seen_normalized:
            continue
        seen_normalized.add(norm)
        
        # Intent classification
        intent_res = detector.detect(question)
        raw_intent = intent_res.primary_intent
        category = _INTENT_CATEGORY_MAP.get(raw_intent, "sales")
        
        # Mask entities
        masked_q = mask_entities(question)
        
        entry = {
            "question": question,
            "masked_question": masked_q,
            "raw_intent": raw_intent,
            "category": category,
            "intent_confidence": intent_res.confidence,
            "source": item["source"],
            "gold_sql": item["gold_sql"]
        }
        
        flat_results.append(entry)
        library[category].append(entry)
        
    # Write flat file (for backward compatibility)
    with open(output_flat, "w", encoding="utf-8") as f:
        json.dump(flat_results, f, indent=2)
        
    # Write library categorized file
    with open(output_library, "w", encoding="utf-8") as f:
        json.dump(library, f, indent=2)
        
    print("\nImport & Classification Summary:")
    print(f"  - Total unique prompts processed: {len(flat_results)}")
    for cat, items in library.items():
        print(f"  - {cat.upper()}: {len(items)} prompts")
    print(f"\nSaved flat library to: {output_flat}")
    print(f"Saved categorized library to: {output_library}")


if __name__ == "__main__":
    import_and_process()
