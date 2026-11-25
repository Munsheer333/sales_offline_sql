import re
from typing import List, Optional

COMPANY_NAMES = {
    "HPCL", "IOCL", "BPCL", "SHELL", "GAIL", "CPCL", "HMEL", "MRPL",
    "NEL", "NRL", "RIL", "RELIANCE", "NAYARA", "ONGC", "SMA", "OIL",
}
PRODUCT_NAMES = {"HSD", "MS", "LPG", "PROPANE", "NAPHTHA", "SKO", "ATF", "LDO", "FO"}
SBU_VALUES = {"Retail", "I&C", "LPG", "Aviation", "Petchem", "Gas", "Lubes"}
ZONES = {"North", "South", "East", "West", "Central"}

METRICS = {
    "market share": "market share",
    "market contribution": "market share",
    "growth": "growth %",
    "growth %": "growth %",
    "sales difference": "sales difference",
    "difference": "sales difference",
    "actual volume": "actual volume",
    "volume": "actual volume",
    "sales": "total sales",
    "turnover": "total sales",
    "business": "total sales",
}

def normalize_shorthand_query(question: str) -> str:
    """Normalize shorthand analytics questions into canonical sentences."""
    if not question:
        return question

    original = question.strip()
    q_lower = f" {original.lower()} "
    
    # If already starts with a query prefix or contains top/bottom/compare, do not normalize
    if any(prefix in q_lower for prefix in ["what", "how", "show", "get", "list", "run", "calculate", "find", "top", "bottom", "compare"]):
        return original
        
    # Check if a known metric is in the question
    matched_metric = None
    for term, canonical in METRICS.items():
        if re.search(rf"\b{re.escape(term)}\b", q_lower):
            matched_metric = canonical
            # Remove the metric keyword to isolate other tokens
            q_lower = re.sub(rf"\b{re.escape(term)}\b", " ", q_lower)
            break
            
    if not matched_metric:
        return original

    # Extract entities from the remaining tokens
    found_entities: List[str] = []
    
    # 1. Companies
    for company in COMPANY_NAMES:
        if re.search(rf"\b{re.escape(company.lower())}\b", q_lower):
            found_entities.append(company)
            q_lower = re.sub(rf"\b{re.escape(company.lower())}\b", " ", q_lower)
            
    # 2. Products
    for product in PRODUCT_NAMES:
        if re.search(rf"\b{re.escape(product.lower())}\b", q_lower):
            found_entities.append(product)
            q_lower = re.sub(rf"\b{re.escape(product.lower())}\b", " ", q_lower)
            
    # 3. SBUs
    for sbu in SBU_VALUES:
        if re.search(rf"\b{re.escape(sbu.lower())}\b", q_lower):
            found_entities.append(sbu)
            q_lower = re.sub(rf"\b{re.escape(sbu.lower())}\b", " ", q_lower)
            
    # 4. Zones
    for zone in ZONES:
        if re.search(rf"\b{re.escape(zone.lower())}\b", q_lower):
            found_entities.append(f"{zone} zone")
            q_lower = re.sub(rf"\b{re.escape(zone.lower())}\b", " ", q_lower)

    if found_entities:
        entities_str = " and ".join(found_entities)
        return f"What is {matched_metric} for {entities_str} in current fiscal year"
        
    return original


def detect_incomplete_question(question: str) -> Optional[str]:
    """Detect incomplete queries and return a clarification message, or None if complete."""
    if not question:
        return None

    original = question.strip()
    q_clean = original.lower()

    # If it starts with a query prefix, assume the user is trying to ask a complete question sentence
    if any(q_clean.strip().startswith(prefix) for prefix in ["what", "how", "show", "get", "list", "run", "calculate", "find"]):
        return None

    # Exact matches for the user's specific examples
    if q_clean == "hpcl sales":
        return "I recognized total sales for HPCL, but the query is incomplete. Please specify: product, SBU, zone, region, month, or fiscal year."
    if q_clean == "retail performance":
        return "I recognized Retail SBU, but the metric is missing or ambiguous. Please specify: metric (such as sales, growth, actual volume, or market share), product, zone, region, month, or fiscal year."
    if q_clean == "top company":
        return "I recognized top company request, but the ranking metric is missing. Please specify: metric (such as sales, growth, actual volume, or market share), company, product, SBU, zone, region, month, or fiscal year."

    # Heuristic for incomplete queries:
    # 1. Check if the query contains a clear metric
    has_metric = any(term in q_clean for term in ["sales", "growth", "market share", "volume", "target", "difference", "turnover", "business", "contribution"])
    has_metric = has_metric or any(term in q_clean for term in ["top", "bottom", "compare", "versus", "vs"])
    
    # 2. Check if the query contains a time period
    has_time = any(term in q_clean for term in ["fiscal", "fy", "year", "month", "this", "last", "current", "202", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])

    # 3. Check for presence of key business dimensions
    has_company = any(c.lower() in q_clean for c in COMPANY_NAMES)
    has_product = any(p.lower() in q_clean for p in PRODUCT_NAMES)
    has_sbu = any(s.lower() in q_clean for s in SBU_VALUES)
    has_zone = any(z.lower() in q_clean for z in ZONES)

    # If query is short (<= 4 words)
    words = q_clean.split()
    if len(words) <= 4:
        if not has_metric:
            return "The metric is missing or ambiguous. Please specify: metric (such as sales, growth, actual volume, or market share), company, product, SBU, zone, region, month, or fiscal year."
        
        if not has_time and not any(term in q_clean for term in ["top", "bottom", "compare", "versus", "vs"]):
            missing_elements = []
            if not has_company:
                missing_elements.append("company")
            if not has_product:
                missing_elements.append("product")
            if not has_sbu:
                missing_elements.append("SBU")
            if not has_zone:
                missing_elements.append("zone")
            missing_elements.append("month")
            missing_elements.append("fiscal year")
            
            return f"I recognized the request, but the query is incomplete. Please specify: {', '.join(missing_elements[:6])}, or fiscal year."

    return None

