import json
import re
import os
from pathlib import Path
from difflib import SequenceMatcher
from collections import defaultdict
from typing import Dict, List, Tuple, Any, Optional

class TrieNode:
    def __init__(self):
        self.children = {}
        self.entities = []  # List of tuples (entity_type, canonical_name)

class Trie:
    def __init__(self):
        self.root = TrieNode()

    def insert(self, alias_words: List[str], entity_type: str, canonical_name: str):
        if not alias_words:
            return
        node = self.root
        for word in alias_words:
            if word not in node.children:
                node.children[word] = TrieNode()
            node = node.children[word]
        # Avoid duplicates
        entity_tuple = (entity_type, canonical_name)
        if entity_tuple not in node.entities:
            node.entities.append(entity_tuple)

    def search_query(self, query_words: List[str]) -> List[Tuple[int, int, str, str]]:
        # Returns list of (start_word_idx, end_word_idx, entity_type, canonical_name)
        matches = []
        n = len(query_words)
        i = 0
        while i < n:
            node = self.root
            j = i
            longest_match = None
            longest_match_len = 0
            while j < n and query_words[j] in node.children:
                node = node.children[query_words[j]]
                j += 1
                if node.entities:
                    longest_match = node.entities
                    longest_match_len = j - i
            
            if longest_match:
                for entity_type, canonical_name in longest_match:
                    matches.append((i, i + longest_match_len, entity_type, canonical_name))
                i += longest_match_len
            else:
                i += 1
        return matches

class FuzzyIndexer:
    def __init__(self, threshold: float = 0.78):
        self.threshold = threshold
        self.candidates = []  # List of (normalized_alias, entity_type, canonical_name)
        self.ngram_to_ids = defaultdict(list)

    def add(self, alias: str, entity_type: str, canonical_name: str):
        self._add_single(alias, entity_type, canonical_name)
        
        # Also index individual words of multi-word alias (skip for fiscal years)
        if entity_type != "fiscal_year":
            words = alias.split()
            if len(words) > 1:
                for w in words:
                    if len(w) >= 3 and w not in {"and", "corporation", "limited", "of", "the", "state", "district", "zone"}:
                        self._add_single(w, entity_type, canonical_name)

    def _add_single(self, alias: str, entity_type: str, canonical_name: str):
        idx = len(self.candidates)
        self.candidates.append((alias, entity_type, canonical_name))
        # Index 2-grams
        for i in range(len(alias) - 1):
            ngram = alias[i:i+2]
            self.ngram_to_ids[ngram].append(idx)

    def fuzzy_match(self, word: str) -> List[Tuple[str, str, float]]:
        if len(word) < 3:
            return []
        candidate_counts = defaultdict(int)
        for i in range(len(word) - 1):
            ngram = word[i:i+2]
            for idx in self.ngram_to_ids.get(ngram, []):
                candidate_counts[idx] += 1

        sorted_candidates = sorted(candidate_counts.items(), key=lambda x: x[1], reverse=True)[:30]
        matches = []
        for idx, count in sorted_candidates:
            alias, entity_type, canonical_name = self.candidates[idx]
            if abs(len(alias) - len(word)) > 3:
                continue
            score = SequenceMatcher(None, word, alias).ratio()
            if score >= self.threshold:
                matches.append((entity_type, canonical_name, score))
        return sorted(matches, key=lambda x: x[2], reverse=True)

class DeterministicEntityResolver:
    def __init__(self, alias_dict_path: str = "alias_dictionary.json"):
        self.trie = Trie()
        self.fuzzy_indexer = FuzzyIndexer()
        
        # Load custom mappings
        self._load_custom_mappings()
        
        # Load main alias dictionary
        if os.path.exists(alias_dict_path):
            try:
                with open(alias_dict_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for entry in data.get("entries", []):
                        entity_type = entry.get("entity_type")
                        # Map to our standard target keys
                        target_type = self._map_entity_type(entity_type)
                        if not target_type:
                            continue
                        
                        canonical = entry.get("canonical_name")
                        alias = entry.get("alias")
                        normalized_alias = self._normalize(alias)
                        
                        if normalized_alias:
                            words = normalized_alias.split()
                            self.trie.insert(words, target_type, canonical)
                            self.fuzzy_indexer.add(normalized_alias, target_type, canonical)
            except Exception as e:
                print(f"Warning: Could not load alias dictionary: {e}")

    def _map_entity_type(self, source_type: str) -> Optional[str]:
        mapping = {
            "companies": "company",
            "products": "product",
            "sbus": "sbu",
            "zones": "zone",
            "districts": "district",
            "fiscal_years": "fiscal_year",
        }
        return mapping.get(source_type)

    def _normalize(self, value: str) -> str:
        normalized = str(value or "").casefold().replace("&", " and ")
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def _load_custom_mappings(self):
        # 0. Companies
        companies = {
            "ONGC": ["ongc", "oil and natural gas corporation"],
            "HPCL": ["hpcl", "hindustan petroleum"],
            "IOCL": ["iocl", "indian oil"],
            "BPCL": ["bpcl", "bharat petroleum"],
            "GAIL": ["gail", "gas authority of india"],
        }
        for company, aliases in companies.items():
            for alias in aliases:
                norm = self._normalize(alias)
                self.trie.insert(norm.split(), "company", company)
                self.fuzzy_indexer.add(norm, "company", company)

        # 0b. Products
        products = {
            "LUBES": ["lubes", "lubricants", "lube", "lubricant"],
            "TMT": ["tmt", "tmt bars", "tmt bar"],
            "MS": ["ms", "motor spirit", "petrol"],
            "HSD": ["hsd", "high speed diesel", "diesel"],
            "SKO": ["sko", "kerosene"],
            "LPG": ["lpg", "liquefied petroleum gas"],
            "ATF": ["atf", "aviation turbine fuel"],
            "FO": ["fo", "furnace oil"],
            "LDO": ["ldo", "light diesel oil"],
            "BITUMEN": ["bitumen", "asphalt"],
            "PROPANE": ["propane"],
            "PROPYLENE": ["propylene"],
            "NAPHTHA": ["naphtha"],
            "CNG": ["cng", "compressed natural gas"],
        }
        for prod, aliases in products.items():
            for alias in aliases:
                norm = self._normalize(alias)
                self.trie.insert(norm.split(), "product", prod)
                self.fuzzy_indexer.add(norm, "product", prod)

        # 0c. Strategic Business Units (SBUs)
        sbus = {
            "RETAIL": ["retail", "ret"],
            "LPG": ["lpg"],
            "I&C": ["i&c", "industrial and consumer", "i and c", "industrial"],
            "LUBES": ["lubes", "lubricants"],
            "AVIATION": ["aviation", "av"],
            "GAS": ["gas", "natural gas"],
        }
        for sbu, aliases in sbus.items():
            for alias in aliases:
                norm = self._normalize(alias)
                self.trie.insert(norm.split(), "sbu", sbu)
                self.fuzzy_indexer.add(norm, "sbu", sbu)

        # 1. State Names & UTs
        states = {
            "Andhra Pradesh": ["andhra pradesh", "ap", "andhra"],
            "Arunachal Pradesh": ["arunachal pradesh", "arunachal", "ar"],
            "Assam": ["assam", "as"],
            "Bihar": ["bihar", "br"],
            "Chhattisgarh": ["chhattisgarh", "cg", "chhatisgarh"],
            "Goa": ["goa", "ga"],
            "Gujarat": ["gujarat", "gj"],
            "Haryana": ["haryana", "hr"],
            "Himachal Pradesh": ["himachal pradesh", "hp", "himachal"],
            "Jammu & Kashmir": ["jammu & kashmir", "jk", "jammu", "kashmir", "jammu and kashmir"],
            "Jharkhand": ["jharkhand", "jh"],
            "Karnataka": ["karnataka", "ka"],
            "Kerala": ["kerala", "kl"],
            "Madhya Pradesh": ["madhya pradesh", "mp", "madhya"],
            "Maharashtra": ["maharashtra", "mh", "mah"],
            "Manipur": ["manipur", "mn"],
            "Meghalaya": ["meghalaya", "ml"],
            "Mizoram": ["mizoram", "mz"],
            "Nagaland": ["nagaland", "nl"],
            "Odisha": ["odisha", "od", "orissa"],
            "Punjab": ["punjab", "pb"],
            "Rajasthan": ["rajasthan", "rj", "raj"],
            "Sikkim": ["sikkim", "sk"],
            "Tamil Nadu": ["tamil nadu", "tn", "tamilnadu"],
            "Telangana": ["telangana", "ts"],
            "Tripura": ["tripura", "tr"],
            "Uttar Pradesh": ["uttar pradesh", "up", "uttar"],
            "Uttarakhand": ["uttarakhand", "uk", "uttaranchal"],
            "West Bengal": ["west bengal", "wb", "bengal"],
            "Delhi": ["delhi", "dl"],
            "Chandigarh": ["chandigarh", "ch"],
            "Ladakh": ["ladakh", "la"],
            "Puducherry": ["puducherry", "py", "pondicherry"],
            "Dadra & Nagar Haveli and Daman & Diu": ["dadra & nagar haveli and daman & diu", "daman & diu", "daman", "diu", "dadra"],
            "Lakshadweep": ["lakshadweep", "ld"],
            "Andaman & Nicobar Islands": ["andaman & nicobar islands", "andaman", "nicobar", "a & n"]
        }
        for state, aliases in states.items():
            for alias in aliases:
                norm = self._normalize(alias)
                self.trie.insert(norm.split(), "state", state)
                self.fuzzy_indexer.add(norm, "state", state)

        # 2. Categories
        categories = {
            "O": ["o", "others", "other"],
            "R": ["r", "retail", "ret"],
            "D": ["d", "direct", "dir"]
        }
        for cat, aliases in categories.items():
            for alias in aliases:
                norm = self._normalize(alias)
                self.trie.insert(norm.split(), "category", cat)
                self.fuzzy_indexer.add(norm, "category", cat)

        # 3. Metrics (Consult Metadata Registry)
        try:
            from core.metadata_registry import MetadataRegistry
            registry = MetadataRegistry()
            metrics = registry.get_all_metrics()
            for metric_key, details in metrics.items():
                aliases = details.get("aliases", [])
                for alias in aliases:
                    norm = self._normalize(alias)
                    self.trie.insert(norm.split(), "metric", metric_key)
                    self.fuzzy_indexer.add(norm, "metric", metric_key)
        except Exception as e:
            print(f"Warning: Could not load metrics from registry: {e}")

        # 4. Months
        months = {
            "January": ["january", "jan"],
            "February": ["february", "feb"],
            "March": ["march", "mar"],
            "April": ["april", "apr"],
            "May": ["may"],
            "June": ["june", "jun"],
            "July": ["july", "jul"],
            "August": ["august", "aug"],
            "September": ["september", "sep", "sept"],
            "October": ["october", "oct"],
            "November": ["november", "nov"],
            "December": ["december", "dec"]
        }
        for month, aliases in months.items():
            for alias in aliases:
                norm = self._normalize(alias)
                self.trie.insert(norm.split(), "month", month)
                self.fuzzy_indexer.add(norm, "month", month)

    def resolve(self, query: str) -> Dict[str, str]:
        normalized = self._normalize(query)
        words = normalized.split()
        
        # Exact matching using Trie
        matches = self.trie.search_query(words)
        
        result = {}
        matched_word_indices = set()
        
        # Populate exact matches
        for start, end, entity_type, canonical in matches:
            result[entity_type] = canonical
            for idx in range(start, end):
                matched_word_indices.add(idx)

        # For remaining unmatched words, run fuzzy matching if they are not common stop words
        stop_words = {
            "in", "for", "of", "and", "the", "during", "with", "between", "to", "show", "get", "what", "is", "by",
            "find", "are", "above", "below", "than", "whose", "which", "each", "every", "per", "has", "have", "had",
            "was", "were", "been", "where", "having", "groupby", "group", "order", "sort", "list", "run", "query",
            "display", "calculate", "compute", "sum", "avg", "count", "min", "max", "total", "average"
        }
        
        for idx, word in enumerate(words):
            if idx in matched_word_indices or word in stop_words or len(word) < 3:
                continue
            
            fuzzy_hits = self.fuzzy_indexer.fuzzy_match(word)
            if fuzzy_hits:
                # Take the highest scoring match for an entity type not already matched
                for entity_type, canonical, score in fuzzy_hits:
                    if entity_type not in result:
                        result[entity_type] = canonical
                        break

        # Resolve fiscal year from explicit mentions or year digits
        if "fiscal_year" not in result:
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
                    result["fiscal_year"] = f"{start_yr}-{end_yr}"
            
            # 2. Match short years like fy25-26 or 25-26
            if "fiscal_year" not in result:
                fy_short = re.search(r'\b(?:fy)?\s*(\d{2})[-/](\d{2})\b', normalized)
                if fy_short:
                    start_yr = 2000 + int(fy_short.group(1))
                    end_yr = 2000 + int(fy_short.group(2))
                    if end_yr == start_yr + 1:
                        result["fiscal_year"] = f"{start_yr}-{end_yr}"

            # 3. Match relative terms like 'current fiscal year' or 'current fy'
            if "fiscal_year" not in result:
                if any(term in normalized for term in ["current fy", "this fy", "current fiscal year", "current financial year"]):
                    # India fiscal year is start_year = today.year if month >= 4 else today.year - 1
                    from datetime import date
                    today = date.today()
                    start_yr = today.year if today.month >= 4 else today.year - 1
                    result["fiscal_year"] = f"{start_yr}-{start_yr + 1}"

            # 4. Match standalone 4-digit year (e.g. 2025)
            if "fiscal_year" not in result:
                year_match = re.search(r'\b(20\d{2})\b', normalized)
                if year_match:
                    year = int(year_match.group(1))
                    month_num = None
                    if "month" in result:
                        month_name = result["month"]
                        month_map = {
                            "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
                            "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12
                        }
                        month_num = month_map.get(month_name)
                    
                    if month_num is not None:
                        start_year = year if month_num >= 4 else year - 1
                    else:
                        start_year = year
                    result["fiscal_year"] = f"{start_year}-{start_year + 1}"
        
        return result
