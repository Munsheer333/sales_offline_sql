import os
import json
import random
import re
from datetime import datetime
from typing import List, Dict, Any, Tuple

def load_json_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json_file(path: str, data: Any):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def generate_prompts(num_variants_per_query: int = 5):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(base_dir, "templates")
    generated_dir = os.path.join(base_dir, "generated")
    
    os.makedirs(generated_dir, exist_ok=True)
    
    golden_path = os.path.join(templates_dir, "golden_queries.json")
    registry_path = os.path.join(templates_dir, "entity_registry.json")
    
    if not os.path.exists(golden_path) or not os.path.exists(registry_path):
        print("Error: templates/golden_queries.json or templates/entity_registry.json not found.")
        return
        
    golden_queries = load_json_file(golden_path)
    registry = load_json_file(registry_path)
    
    generated_prompts = []
    
    # Common products fallback
    products = ["HSD", "MS", "LPG", "PROPANE", "NAPHTHA", "SKO", "ATF", "LDO", "FO"]
    
    print(f"Generating benchmark prompts (target: {num_variants_per_query} variants per query)...")
    
    for idx, item in enumerate(golden_queries):
        question_tpl = item["question"]
        sql_tpl = item["sql"]
        category = item.get("category", "unclassified")
        complexity = item.get("complexity", "MEDIUM")
        
        # Add the original golden query first
        generated_prompts.append({
            "category": category,
            "question": question_tpl,
            "sql": sql_tpl,
            "complexity": complexity,
            "is_golden": True
        })
        
        variants_created = 0
        attempts = 0
        seen_questions = {question_tpl.lower()}
        
        while variants_created < num_variants_per_query and attempts < 100:
            attempts += 1
            new_question = question_tpl
            new_sql = sql_tpl
            changes_made = 0
            
            # 1. Swap Company
            for co in registry["companies"]:
                if re.search(rf"\b{re.escape(co)}\b", new_question, re.IGNORECASE):
                    replacement = random.choice(registry["companies"])
                    new_question = re.sub(rf"\b{re.escape(co)}\b", replacement, new_question, flags=re.IGNORECASE)
                    new_sql = re.sub(rf"'{re.escape(co)}'", f"'{replacement}'", new_sql)
                    new_sql = re.sub(rf"\b{re.escape(co)}\b", replacement, new_sql)
                    changes_made += 1
                    break
                    
            # 2. Swap SBU
            for sbu in registry["sbus"]:
                if re.search(rf"\b{re.escape(sbu)}\b", new_question, re.IGNORECASE):
                    replacement = random.choice(registry["sbus"])
                    new_question = re.sub(rf"\b{re.escape(sbu)}\b", replacement, new_question, flags=re.IGNORECASE)
                    new_sql = re.sub(rf"'{re.escape(sbu)}'", f"'{replacement}'", new_sql)
                    new_sql = re.sub(rf"\b{re.escape(sbu)}\b", replacement, new_sql)
                    changes_made += 1
                    break
                    
            # 3. Swap Zone
            for zone in registry["zones"]:
                clean_zone = zone.replace(" Zone", "").strip()
                if clean_zone and re.search(rf"\b{re.escape(clean_zone)}\b", new_question, re.IGNORECASE):
                    replacement_zone = random.choice(registry["zones"])
                    clean_replacement = replacement_zone.replace(" Zone", "").strip()
                    new_question = re.sub(rf"\b{re.escape(clean_zone)}\b", clean_replacement, new_question, flags=re.IGNORECASE)
                    new_sql = re.sub(rf"'{re.escape(zone)}'", f"'{replacement_zone}'", new_sql)
                    new_sql = re.sub(rf"'{re.escape(clean_zone)}'", f"'{clean_replacement}'", new_sql)
                    new_sql = re.sub(rf"\b{re.escape(zone)}\b", replacement_zone, new_sql)
                    new_sql = re.sub(rf"\b{re.escape(clean_zone)}\b", clean_replacement, new_sql)
                    changes_made += 1
                    break
                    
            # 4. Swap Product
            for prod in products:
                if re.search(rf"\b{re.escape(prod)}\b", new_question, re.IGNORECASE):
                    replacement = random.choice(products)
                    new_question = re.sub(rf"\b{re.escape(prod)}\b", replacement, new_question, flags=re.IGNORECASE)
                    new_sql = re.sub(rf"'{re.escape(prod)}'", f"'{replacement}'", new_sql)
                    new_sql = re.sub(rf"\b{re.escape(prod)}\b", replacement, new_sql)
                    changes_made += 1
                    break
                    
            # 5. Swap Year
            for yr in registry["years"]:
                if yr in new_question:
                    replacement = random.choice(registry["years"])
                    new_question = new_question.replace(yr, replacement)
                    new_sql = new_sql.replace(yr, replacement)
                    changes_made += 1
                    break
            
            # Save unique variation
            if changes_made > 0 and new_question.lower() not in seen_questions:
                seen_questions.add(new_question.lower())
                generated_prompts.append({
                    "category": category,
                    "question": new_question,
                    "sql": new_sql,
                    "complexity": complexity,
                    "is_golden": False
                })
                variants_created += 1
                
    output_path = os.path.join(generated_dir, "generated_prompts.json")
    save_json_file(output_path, generated_prompts)
    
    print(f"Generated {len(generated_prompts)} total benchmark prompts ({len(golden_queries)} golden + {len(generated_prompts) - len(golden_queries)} variants).")
    print(f"Prompts file successfully saved at: {output_path}")

if __name__ == "__main__":
    generate_prompts()
