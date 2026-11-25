"""
NLPSQL SYSTEM — SYNTHETIC BENCHMARK PROMPT GENERATOR
=====================================================
Generates a diverse set of synthetic benchmark prompts using combinatorial values
from entity_registry.json for:
  - sales
  - growth
  - market share
  - comparison
  - ranking

Stores generated prompts in benchmark/generated/synthetic_prompts.json.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Dict, List, Any

# Root folder setup
ROOT_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT_DIR / "benchmark" / "templates" / "entity_registry.json"
OUTPUT_PATH = ROOT_DIR / "benchmark" / "generated" / "synthetic_prompts.json"


def load_registry() -> Dict[str, List[str]]:
    if not REGISTRY_PATH.exists():
        # Fallback list if registry missing
        return {
            "companies": ["HPCL", "IOCL", "BPCL", "RIL", "GAIL"],
            "sbus": ["Retail", "LPG", "I&C", "LUBES"],
            "regions": ["MUMBAI RET RO", "DELHI RET RO", "PUNE RET RO"],
            "zones": ["NZ", "WZ", "SZ", "EZ", "NCZ"],
            "years": ["2024-2025", "2025-2026", "2021-2022", "2022-2023"]
        }
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def generate_synthetic_library():
    registry = load_registry()
    
    companies = registry.get("companies", ["HPCL", "IOCL", "BPCL"])
    sbus = registry.get("sbus", ["Retail", "LPG", "I&C"])
    regions = registry.get("regions", ["MUMBAI RET RO", "DELHI RET RO"])
    zones = registry.get("zones", ["North", "West", "South", "East", "NZ", "WZ"])
    years = [y for y in registry.get("years", []) if "FY" not in y] # clean year intervals
    
    # Filter/clean regions and zones
    clean_zones = [z.replace(" Zone", "").strip() for z in zones]
    clean_zones = list(set(clean_zones))
    
    products = ["HSD", "MS", "LPG", "PROPANE", "NAPHTHA", "SKO", "ATF", "LDO", "FO"]
    
    synthetic_prompts = []
    
    # ── 1. Sales Prompts (Target: 40) ─────────────────────────────────────────
    sales_templates = [
        "What is the total sales for {company} in SBU {sbu} for {year}?",
        "Get actual sales of {product} for {company} in {region} during {year}.",
        "Find total actual volume of {product} in {zone} for {year}.",
        "Show total sales in TMT for {company} SBU {sbu} in {year}.",
        "What is the cumulative actual sales for SBU {sbu} in {zone} during {year}?"
    ]
    
    for i in range(40):
        tpl = random.choice(sales_templates)
        company = random.choice(companies)
        sbu = random.choice(sbus)
        product = random.choice(products)
        region = random.choice(regions)
        zone = random.choice(clean_zones)
        year = random.choice(years)
        
        prompt = tpl.format(
            company=company, sbu=sbu, product=product, region=region, zone=zone, year=year
        )
        synthetic_prompts.append({
            "category": "sales",
            "question": prompt,
            "complexity": "MEDIUM"
        })

    # ── 2. Growth Prompts (Target: 40) ────────────────────────────────────────
    growth_templates = [
        "Show growth % of {company} in SBU {sbu} from {year1} to {year2}.",
        "Compare sales difference for {product} under {sbu} from {year1} to {year2}.",
        "What is the sales increase percentage for {company} from {year1} to {year2}?",
        "Calculate growth percentage of actual sales for {product} in {zone} comparing {year1} and {year2}.",
        "Show volume growth trend of SBU {sbu} in {region} from {year1} to {year2}."
    ]
    
    for i in range(40):
        tpl = random.choice(growth_templates)
        company = random.choice(companies)
        sbu = random.choice(sbus)
        product = random.choice(products)
        region = random.choice(regions)
        zone = random.choice(clean_zones)
        # Select two distinct years ordered chronologically
        y1, y2 = sorted(random.sample(years, 2))
        
        prompt = tpl.format(
            company=company, sbu=sbu, product=product, region=region, zone=zone, year1=y1, year2=y2
        )
        synthetic_prompts.append({
            "category": "growth",
            "question": prompt,
            "complexity": "HARD"
        })

    # ── 3. Market Share Prompts (Target: 40) ──────────────────────────────────
    market_share_templates = [
        "What is the market share of {company} in SBU {sbu} for {year}?",
        "Find company wise market share percentage in {zone} during {year}.",
        "Show market contribution of {company} in {region} for SBU {sbu} during {year}.",
        "Which company has the highest market share of {product} in {year}?",
        "Provide market share breakdown for all companies under SBU {sbu} for {year}."
    ]
    
    for i in range(40):
        tpl = random.choice(market_share_templates)
        company = random.choice(companies)
        sbu = random.choice(sbus)
        product = random.choice(products)
        region = random.choice(regions)
        zone = random.choice(clean_zones)
        year = random.choice(years)
        
        prompt = tpl.format(
            company=company, sbu=sbu, product=product, region=region, zone=zone, year=year
        )
        synthetic_prompts.append({
            "category": "market_share",
            "question": prompt,
            "complexity": "HARD"
        })

    # ── 4. Comparison Prompts (Target: 40) ────────────────────────────────────
    comparison_templates = [
        "Compare {company1} vs {company2} sales of {product} in {zone} for {year}.",
        "Show sales comparison of SBU {sbu1} and SBU {sbu2} in {region} for {year}.",
        "Compare {company1} and {company2} actual volume in {zone} during {year}.",
        "How do SBU {sbu1} sales compare with SBU {sbu2} for {company} in {year}?",
        "Compare regional sales of {product} in {region1} vs {region2} during {year}."
    ]
    
    for i in range(40):
        tpl = random.choice(comparison_templates)
        co1, co2 = random.sample(companies, 2)
        sbu1, sbu2 = random.sample(sbus, 2)
        reg1, reg2 = random.sample(regions, 2)
        company = random.choice(companies)
        product = random.choice(products)
        zone = random.choice(clean_zones)
        year = random.choice(years)
        
        prompt = tpl.format(
            company1=co1, company2=co2, sbu1=sbu1, sbu2=sbu2,
            region1=reg1, region2=reg2, company=company,
            product=product, region=reg1, zone=zone, year=year
        )
        synthetic_prompts.append({
            "category": "comparison",
            "question": prompt,
            "complexity": "MEDIUM"
        })

    # ── 5. Ranking Prompts (Target: 40) ───────────────────────────────────────
    ranking_templates = [
        "Rank all companies by sales of {product} in {zone} for {year}.",
        "Show top 5 regions by actual sales of {product} for {company} in {year}.",
        "Get the highest 3 products by volume in {region} during {year}.",
        "Rank SBUs based on total sales inside {zone} for {year}.",
        "Show bottom 3 companies by market contribution % under SBU {sbu} in {year}."
    ]
    
    for i in range(40):
        tpl = random.choice(ranking_templates)
        company = random.choice(companies)
        sbu = random.choice(sbus)
        product = random.choice(products)
        region = random.choice(regions)
        zone = random.choice(clean_zones)
        year = random.choice(years)
        
        prompt = tpl.format(
            company=company, sbu=sbu, product=product, region=region, zone=zone, year=year
        )
        synthetic_prompts.append({
            "category": "ranking",
            "question": prompt,
            "complexity": "MEDIUM"
        })

    # Save output
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(synthetic_prompts, f, indent=2)
        
    print(f"Generated {len(synthetic_prompts)} synthetic prompts saved to: {OUTPUT_PATH}")
    for cat in ["sales", "growth", "market_share", "comparison", "ranking"]:
        count = sum(1 for p in synthetic_prompts if p["category"] == cat)
        print(f"  - {cat.upper()}: {count} prompts")


if __name__ == "__main__":
    generate_synthetic_library()
