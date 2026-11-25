import os
import json
from typing import Dict, List

def generate_entity_registry():
    # Paths
    base_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(base_dir)
    business_metadata_path = os.path.join(root_dir, "business_metadata.json")
    templates_dir = os.path.join(base_dir, "templates")
    registry_path = os.path.join(templates_dir, "entity_registry.json")
    
    os.makedirs(templates_dir, exist_ok=True)
    
    if not os.path.exists(business_metadata_path):
        print(f"Error: business_metadata.json not found at {business_metadata_path}")
        return
        
    with open(business_metadata_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
        
    # Helper to extract canonical names and aliases
    def extract_values(items: List[Dict]) -> List[str]:
        values = set()
        for item in items:
            if "canonical_name" in item:
                values.add(item["canonical_name"])
            if "aliases" in item:
                for alias in item["aliases"]:
                    values.add(alias)
            if "value" in item:
                values.add(item["value"])
        return sorted(list(values))

    registry = {
        "companies": extract_values(meta.get("companies", [])),
        "sbus": extract_values(meta.get("sbus", [])),
        "regions": extract_values(meta.get("regions", [])),
        "zones": extract_values(meta.get("zones", [])),
        "years": extract_values(meta.get("fiscal_years", []))
    }
    
    # Fallbacks if any registry is empty
    if not registry["companies"]:
        registry["companies"] = ["HPCL", "IOCL", "BPCL", "SHELL", "GAIL", "CPCL", "HMEL", "MRPL", "RIL", "OIL"]
    if not registry["sbus"]:
        registry["sbus"] = ["Retail", "I&C", "LPG", "Aviation", "Petchem", "Gas", "Lubes"]
    if not registry["zones"]:
        registry["zones"] = ["North", "South", "East", "West", "Central"]
    if not registry["years"]:
        registry["years"] = ["2021-2022", "2022-2023", "2023-2024", "2024-2025", "2025-2026"]
        
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
        
    print(f"Benchmark entity registry successfully generated at: {registry_path}")
    print(f"Summary of registered entities:")
    for key, val in registry.items():
        print(f"  - {key}: {len(val)} items")

if __name__ == "__main__":
    generate_entity_registry()
