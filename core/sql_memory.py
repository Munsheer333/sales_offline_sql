import os
import json
import math
import re
from collections import Counter
from typing import Dict, Any, List, Optional, Tuple

class BM25Ranker:
    def __init__(self, corpus: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = len(corpus)
        self.avg_doc_len = sum(len(doc) for doc in corpus) / self.corpus_size if self.corpus_size > 0 else 0
        self.doc_lens = [len(doc) for doc in corpus]
        self.doc_freqs = []
        self.nd = {}  # term -> number of docs containing term
        
        for doc in corpus:
            freqs = Counter(doc)
            self.doc_freqs.append(freqs)
            for term in freqs.keys():
                self.nd[term] = self.nd.get(term, 0) + 1
                
    def idf(self, term: str) -> float:
        n = self.nd.get(term, 0)
        return math.log((self.corpus_size - n + 0.5) / (n + 0.5) + 1.0)

    def score(self, query: List[str], doc_idx: int) -> float:
        score = 0.0
        doc_freq = self.doc_freqs[doc_idx]
        d_len = self.doc_lens[doc_idx]
        
        for term in query:
            if term not in doc_freq:
                continue
            f = doc_freq[term]
            idf_val = self.idf(term)
            numerator = f * (self.k1 + 1)
            denominator = f + self.k1 * (1 - self.b + self.b * (d_len / self.avg_doc_len))
            score += idf_val * (numerator / denominator)
        return score

class SQLMemory:
    def __init__(self, memory_path: str = None):
        if memory_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            memory_path = os.path.join(base_dir, "sql_memory.json")
            
        self.memory_path = memory_path
        self.examples: List[Dict[str, Any]] = []
        self.load()

    def load(self):
        if os.path.exists(self.memory_path):
            with open(self.memory_path, "r", encoding="utf-8") as f:
                self.examples = json.load(f)
        else:
            self.examples = []

    def _tokenize(self, text: str) -> List[str]:
        # Simple lowercase tokenization
        cleaned = re.sub(r"[^a-zA-Z0-9\s-]", "", str(text or "").lower())
        return cleaned.split()

    def _calculate_metadata_score(self, query_entities: Dict[str, Any], example_entities: Dict[str, Any]) -> float:
        # Score based on overlap of entity types/keys
        score = 0.0
        for k in query_entities.keys():
            if k in example_entities:
                score += 1.0  # Key match bonus
                # Extra bonus for matching value
                val_query = query_entities[k]
                val_example = example_entities[k]
                if str(val_query).lower() == str(val_example).lower():
                    score += 1.5
        return score

    def _entities_equal(self, e1: Dict[str, Any], e2: Dict[str, Any]) -> bool:
        # Normalize: keys to lowercase, values to sorted list of lowercase strings or single lowercase string.
        # Ignore empty values/None.
        def clean_val(v):
            if v is None:
                return []
            if isinstance(v, list):
                return sorted(str(x).strip().lower() for x in v if x)
            val_str = str(v).strip().lower()
            return [val_str] if val_str else []

        n1 = {k.strip().lower(): clean_val(v) for k, v in e1.items()}
        n2 = {k.strip().lower(): clean_val(v) for k, v in e2.items()}
        
        # Filter out empty lists/values
        n1 = {k: v for k, v in n1.items() if v}
        n2 = {k: v for k, v in n2.items() if v}
        
        return n1 == n2

    def retrieve(self, query: str, intent: str, entities: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # Filter candidates by intent and exact entity match
        candidates = []
        for idx, ex in enumerate(self.examples):
            if ex.get("intent", "").upper() == intent.upper():
                if self._entities_equal(entities, ex.get("entities", {})):
                    candidates.append((idx, ex))

        if not candidates:
            return None

        # Setup BM25 Ranker on candidates
        candidate_corpus = [self._tokenize(ex["question"]) for _, ex in candidates]
        ranker = BM25Ranker(candidate_corpus)
        
        query_tokens = self._tokenize(query)
        
        # Score and rank candidates
        scored_candidates = []
        for ranker_idx, (orig_idx, ex) in enumerate(candidates):
            bm25_score = ranker.score(query_tokens, ranker_idx)
            meta_score = self._calculate_metadata_score(entities, ex.get("entities", {}))
            total_score = bm25_score + meta_score
            scored_candidates.append((total_score, ex))

        # Sort by total score descending
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        
        # Return top match
        if scored_candidates:
            return scored_candidates[0][1]
            
        return None
