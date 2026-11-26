import re
from collections import defaultdict
from typing import Dict, Any, List, Optional
from core.entity_resolver import DeterministicEntityResolver
from core.intent_classifier import DeterministicIntentClassifier

class LogicalQueryPlanner:
    def __init__(self, alias_dict_path: str = "alias_dictionary.json"):
        self.resolver = DeterministicEntityResolver(alias_dict_path)
        self.classifier = DeterministicIntentClassifier()

    def _get_exact_casing(self, query: str, term: str) -> str:
        # Escapes special characters and finds the term case-insensitively in the query
        escaped = re.escape(term)
        match = re.search(rf"\b{escaped}\b", query, re.IGNORECASE)
        if match:
            return match.group(0)
        # Fallback to simple search
        match = re.search(escaped, query, re.IGNORECASE)
        if match:
            return match.group(0)
        return term

    def _format_fy(self, fy: str) -> str:
        # Convert "2024-2025" to "2024-25"
        if fy and "-" in fy:
            parts = fy.split("-")
            if len(parts) == 2 and len(parts[0]) == 4 and len(parts[1]) == 4:
                return f"{parts[0]}-{parts[1][2:]}"
        return fy

    def plan(self, query: str) -> Dict[str, Any]:
        # 1. Classify Intent
        intent_info = self.classifier.classify(query)
        intent = intent_info["intent"]

        # 2. Extract Entities (Trie + Fuzzy)
        normalized = self.resolver._normalize(query)
        words = normalized.split()
        
        matches = self.resolver.trie.search_query(words)
        
        entity_matches = []  # list of (entity_type, canonical, raw_text, start_idx)
        matched_word_indices = set()
        
        # Exact matching
        for start, end, entity_type, canonical in matches:
            raw_text = " ".join(words[start:end])
            entity_matches.append((entity_type, canonical, raw_text, start))
            for idx in range(start, end):
                matched_word_indices.add(idx)

        # Fuzzy matching
        stop_words = {
            "in", "for", "of", "and", "the", "during", "with", "between", "to", "show", "get", "what", "is", "by",
            "find", "are", "above", "below", "than", "whose", "which", "each", "every", "per", "has", "have", "had",
            "was", "were", "been", "where", "having", "groupby", "group", "order", "sort", "list", "run", "query",
            "display", "calculate", "compute", "sum", "avg", "count", "min", "max", "total", "average"
        }
        for idx, word in enumerate(words):
            if idx in matched_word_indices or word in stop_words or len(word) < 3:
                continue
            
            fuzzy_hits = self.resolver.fuzzy_indexer.fuzzy_match(word)
            if fuzzy_hits:
                entity_type, canonical, score = fuzzy_hits[0]
                entity_matches.append((entity_type, canonical, word, idx))

        # Group matched entities by entity_type
        groups = defaultdict(list)
        q_lower_raw = query.lower()
        kl_uom_context = (
            "kerala" not in q_lower_raw
            and "state" not in q_lower_raw
            and bool(re.search(r"\b(?:in|quantity|target|volume|sales)\s+kl\b|\bkl\s+(?:from|for|in)\b", q_lower_raw))
        )
        for entity_type, canonical, raw, pos in entity_matches:
            if entity_type == "state" and canonical == "Kerala" and raw == "kl" and kl_uom_context:
                continue
            groups[entity_type].append({
                "canonical": canonical,
                "raw": raw,
                "pos": pos
            })

        # 3. Determine Metric
        metric = "sales"
        if "metric" in groups:
            metric = groups["metric"][0]["canonical"]

        # 4. Determine Dimension & Comparison list
        dimension = None
        dimensions = []
        comparison_dimension = None
        comparison = []
        
        # Check if fiscal_year_resolver finds multiple fiscal years
        # Handles queries like "Compare FY 2023-2024 and FY 2024-2025", "2023-2024 vs 2024-2025"
        try:
            from fiscal_year_resolver import resolve_fiscal_years
            fy_res = resolve_fiscal_years(query)
            detected_fys = fy_res.get("fiscal_years", [])
        except Exception:
            detected_fys = []

        q_lower = query.lower()
        is_two_fy = bool(re.search(r'\b(?:between|across|for|over|in)\s+(?:the\s+)?(?:two|2|last\s+two|past\s+two)\s*(?:fiscal\s+years?|financial\s+years?|fys?|years?)\b', q_lower)) or \
                    bool(re.search(r'\b(?:two|2|last\s+two|past\s+two)\s+(?:fiscal\s+years?|financial\s+years?|fys?|years?)\b', q_lower)) or \
                    bool(re.search(r'\bbetween\s+(?:the\s+)?(?:fiscal\s+years?|financial\s+years?|years?)\b', q_lower))

        # Check for breakdown dimension (e.g. product performance, company performance)
        breakdown_dim = None
        has_specific_product = any(len(v.get("canonical", "")) > 0 for v in groups.get("product", []))
        has_specific_company = any(len(v.get("canonical", "")) > 0 for v in groups.get("company", []))
        has_specific_sbu = any(len(v.get("canonical", "")) > 0 for v in groups.get("sbu", []))
        has_specific_state = any(len(v.get("canonical", "")) > 0 for v in groups.get("state", []))
        has_specific_zone = any(len(v.get("canonical", "")) > 0 for v in groups.get("zone", []))
        has_specific_region = any(len(v.get("canonical", "")) > 0 for v in groups.get("region", []))

        if any(k in q_lower for k in ["product performance", "products performance", "by product", "per product", "product sales"]) or \
           (("product" in q_lower or "products" in q_lower) and not has_specific_product):
            breakdown_dim = "product"
        elif any(k in q_lower for k in ["company performance", "companies performance", "by company", "per company", "company sales"]) or \
             (("company" in q_lower or "companies" in q_lower) and not has_specific_company):
            breakdown_dim = "company"
        elif any(k in q_lower for k in ["sbu performance", "sbus performance", "by sbu", "per sbu", "sbu sales"]) or \
             (("sbu" in q_lower or "sbus" in q_lower) and not has_specific_sbu):
            breakdown_dim = "sbu"
        elif any(k in q_lower for k in ["state performance", "states performance", "by state", "per state", "state sales"]) or \
             ("states" in q_lower or ("state" in q_lower and not has_specific_state)):
            breakdown_dim = "state"
        elif any(k in q_lower for k in ["zone performance", "zones performance", "by zone", "per zone", "zone sales"]) or \
             ("zones" in q_lower or ("zone" in q_lower and not has_specific_zone)):
            breakdown_dim = "zone"
        elif any(k in q_lower for k in ["region performance", "regions performance", "by region", "per region", "region sales"]) or \
             ("regions" in q_lower or ("region" in q_lower and not has_specific_region)):
            breakdown_dim = "region"

        if is_two_fy and breakdown_dim:
            intent = "COMPARE"
            dimension = breakdown_dim
            comparison_dimension = "fiscal_year"
            dimensions = [breakdown_dim, "fiscal_year"]
            comparison = ["2023-2024", "2024-2025"]
        elif detected_fys and len(detected_fys) >= 2 and breakdown_dim:
            intent = "COMPARE"
            dimension = breakdown_dim
            comparison_dimension = "fiscal_year"
            dimensions = [breakdown_dim, "fiscal_year"]
            comparison = list(detected_fys)
        elif len(detected_fys) >= 2:
            dimension = "fiscal_year"
            comparison_dimension = "fiscal_year"
            dimensions = ["fiscal_year"]
            comparison = list(detected_fys)
        else:
            # Check if any group has multiple distinct values (indicates comparison across that dimension)
            multi_value_type = None
            for etype, values in groups.items():
                if etype != "metric":
                    distinct_canonicals = list(dict.fromkeys(v["canonical"] for v in values))
                    if len(distinct_canonicals) > 1:
                        multi_value_type = etype
                        break
                    
            if multi_value_type:
                dimension = multi_value_type
                # Extract comparison items using original query casing
                seen = set()
                for item in groups[multi_value_type]:
                    canonical = item["canonical"]
                    if canonical not in seen:
                        seen.add(canonical)
                        raw_casing = self._get_exact_casing(query, item["raw"])
                        comparison.append(raw_casing)
            elif intent == "COMPARE":
                # Extract comparison entities syntactically from the question
                # Handles: "Compare LUBES and TMT sales", "Compare LUBES vs TMT", "Difference between LUBES and TMT", etc.
                comp_items = []
                
                # Pattern 1: Compare <item1>, <item2> and/vs <itemN> [sales/volume]
                m_list = re.search(
                    r'\bcompare\s+(?:sales\s+of\s+)?(?:(?:the\s+)?(?:product|company|sbu|state|zone|district)s?\s+)?(.+?)(?:\s+sales|\s+volume|\s*$|\.)',
                    query,
                    re.IGNORECASE
                )
                if m_list:
                    raw_seg = m_list.group(1).strip()
                    tokens = [t.strip() for t in re.split(r',\s*|\s+(?:and|vs|versus|with)\s+', raw_seg, flags=re.IGNORECASE) if t.strip()]
                    comp_items = [t for t in tokens if t.lower() not in {"the", "all", "each", "total", "actual", "sales", "volume"} and len(t) >= 2]
                
                if not comp_items or len(comp_items) < 2:
                    # Pattern 2: <item1> vs/versus <item2>
                    m_vs = re.search(r'\b([A-Za-z0-9_\-\&]+)\s+(?:vs|versus)\s+([A-Za-z0-9_\-\&]+)\b', query, re.IGNORECASE)
                    if m_vs:
                        comp_items = [m_vs.group(1).strip(), m_vs.group(2).strip()]

                if not comp_items or len(comp_items) < 2:
                    # Pattern 3: difference/comparison between <item1> and <item2>
                    m_diff = re.search(r'\b(?:difference\s+between|comparison\s+(?:of|between)|between)\s+([A-Za-z0-9_\-\&]+)\s+and\s+([A-Za-z0-9_\-\&]+)\b', query, re.IGNORECASE)
                    if m_diff:
                        comp_items = [m_diff.group(1).strip(), m_diff.group(2).strip()]

                if comp_items and len(comp_items) >= 2:
                    target_etype = None
                    
                    # 1. Check if comp_items look like fiscal years
                    try:
                        from fiscal_year_resolver import resolve_fiscal_years
                        items_fys = []
                        for ci in comp_items:
                            rfy = resolve_fiscal_years(ci).get("fiscal_years", [])
                            if rfy:
                                items_fys.extend(rfy)
                        if len(items_fys) >= 2:
                            target_etype = "fiscal_year"
                            comp_items = items_fys
                    except Exception:
                        pass
                    
                    # 2. Check if comp_items are months
                    if not target_etype:
                        month_names = {"january", "february", "march", "april", "may", "june", 
                                       "july", "august", "september", "october", "november", "december",
                                       "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
                        if any(ci.lower() in month_names for ci in comp_items):
                            target_etype = "month"

                    # 3. Check if any item matches an existing entity in groups
                    if not target_etype:
                        for item in comp_items:
                            for etype, vals in groups.items():
                                if etype == "metric":
                                    continue
                                if any(v["raw"].lower() == item.lower() or v["canonical"].lower() == item.lower() for v in vals):
                                    target_etype = etype
                                    break
                            if target_etype:
                                break
                    
                    # 4. Fallback to query keyword hints
                    if not target_etype:
                        q_lower = query.lower()
                        if any(k in q_lower for k in ["fiscal year", "financial year", "fy", "year", "years"]):
                            target_etype = "fiscal_year"
                        elif any(k in q_lower for k in ["month", "months"]):
                            target_etype = "month"
                        elif any(k in q_lower for k in ["company", "companies"]):
                            target_etype = "company"
                        elif any(k in q_lower for k in ["state", "states"]):
                            target_etype = "state"
                        elif any(k in q_lower for k in ["sbu", "sbus"]):
                            target_etype = "sbu"
                        elif any(k in q_lower for k in ["zone", "zones"]):
                            target_etype = "zone"
                        elif any(k in q_lower for k in ["region", "regions"]):
                            target_etype = "region"
                        else:
                            target_etype = "product"
                            
                    dimension = target_etype
                    if target_etype == "fiscal_year":
                        comparison = [item.strip() for item in comp_items]
                    else:
                        comparison = [item.upper() for item in comp_items]
                    
                    # Update groups so that filters and resolvers are properly aligned
                    groups[target_etype] = [
                        {"canonical": item if target_etype == "fiscal_year" else item.upper(), "raw": item, "pos": i}
                        for i, item in enumerate(comp_items)
                    ]

        if not dimension and breakdown_dim:
            dimension = breakdown_dim

        if not dimension:
            # Fallback check for dimension keywords if no multiple values found
            dimension_keywords = {
                "state": "state", "states": "state",
                "company": "company", "companies": "company",
                "product": "product", "products": "product",
                "district": "district", "districts": "district",
                "zone": "zone", "zones": "zone",
                "month": "month", "months": "month",
                "fiscal_year": "fiscal_year", "fy": "fiscal_year"
            }
            norm_lower = query.lower()
            for kw, dtype in dimension_keywords.items():
                if re.search(rf"\b{kw}\b", norm_lower):
                    dimension = dtype
                    break

        # 5. Populate Filters
        filters = {}
        comp_set = {str(c).lower() for c in comparison}
        for etype, values in groups.items():
            # Skip metric and comparison dimension
            if etype == "metric" or etype == dimension:
                continue
            
            # Filter out any values that were part of the comparison entity tokens
            valid_values = [
                v for v in values
                if v["canonical"].lower() not in comp_set and v["raw"].lower() not in comp_set
            ]
            if valid_values:
                canonical_val = valid_values[0]["canonical"]
                if etype == "fiscal_year":
                    filters["fiscal_year"] = self._format_fy(canonical_val)
                else:
                    filters[etype] = canonical_val

        # Support extracting fiscal year directly from query string via regex IF NOT comparing fiscal_year
        if dimension != "fiscal_year" and "fiscal_year" not in filters:
            # 1. Match 2025-2026 or 2025-26 or FY2025-26
            fy_match = re.search(r'\b(?:fy)?\s*(20\d{2})[-/](\d{2,4})\b', normalized)
            if fy_match:
                start_yr = int(fy_match.group(1))
                end_val = fy_match.group(2)
                if len(end_val) == 2:
                    end_yr = 2000 + int(end_val)
                else:
                    end_yr = int(end_val)
                if end_yr == start_yr + 1:
                    filters["fiscal_year"] = f"{start_yr}-{str(end_yr)[2:]}"
            
            # 2. Match short years like fy25-26 or 25-26
            if "fiscal_year" not in filters:
                fy_short = re.search(r'\b(?:fy)?\s*(\d{2})[-/](\d{2})\b', normalized)
                if fy_short:
                    start_yr = 2000 + int(fy_short.group(1))
                    end_yr = 2000 + int(fy_short.group(2))
                    if end_yr == start_yr + 1:
                        filters["fiscal_year"] = f"{start_yr}-{str(end_yr)[2:]}"

            # 3. Match relative terms like 'current fiscal year' or 'current fy'
            if "fiscal_year" not in filters:
                if any(term in normalized for term in ["current fy", "this fy", "current fiscal year", "current financial year"]):
                    from datetime import date
                    today = date.today()
                    start_yr = today.year if today.month >= 4 else today.year - 1
                    filters["fiscal_year"] = f"{start_yr}-{str(start_yr + 1)[2:]}"

            # 4. Match standalone 4-digit year (e.g. 2025)
            if "fiscal_year" not in filters:
                year_match = re.search(r'\b(20\d{2})\b', normalized)
                if year_match:
                    year = int(year_match.group(1))
                    month_num = None
                    if "month" in filters:
                        month_name = filters["month"]
                        month_map = {
                            "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
                            "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12
                        }
                        month_num = month_map.get(month_name)
                    
                    if month_num is not None:
                        start_year = year if month_num >= 4 else year - 1
                    else:
                        start_year = year
                    filters["fiscal_year"] = f"{start_year}-{str(start_year + 1)[2:]}"

        # Final guarantee: never filter on the active comparison dimension
        if dimension and dimension in filters:
            del filters[dimension]
        if comparison_dimension and comparison_dimension in filters:
            del filters[comparison_dimension]

        return {
            "intent": intent,
            "metric": metric,
            "dimension": dimension,
            "dimensions": dimensions if dimensions else ([dimension] if dimension else []),
            "comparison_dimension": comparison_dimension or dimension,
            "filters": filters,
            "comparison": comparison
        }
