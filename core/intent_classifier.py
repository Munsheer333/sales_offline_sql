import re
from typing import Dict, Union, Tuple, List, Callable

class IntentRule:
    def __init__(self, intent: str, confidence: float, keywords: List[str], regex_patterns: List[str] = None):
        self.intent = intent
        self.confidence = confidence
        self.keywords = [kw.lower() for kw in keywords]
        self.regexes = [re.compile(pat, re.IGNORECASE) for pat in regex_patterns] if regex_patterns else []

    def matches(self, normalized_text: str) -> bool:
        # Check simple substring keyword match first
        for kw in self.keywords:
            # Match as a word boundary or substring depending on length/type
            if kw in normalized_text:
                return True
        # Check regexes
        for rx in self.regexes:
            if rx.search(normalized_text):
                return True
        return False

class DeterministicIntentClassifier:
    def __init__(self):
        # Define rules in order of priority (specific to generic)
        self.rules: List[IntentRule] = [
            # 1. MoM / YoY (highly specific growth metrics)
            IntentRule(
                "MOM", 0.99,
                ["month over month", "month-over-month", "mom", "m-o-m", "monthly growth", "mom trend"]
            ),
            IntentRule(
                "YOY", 0.99,
                ["year over year", "year-over-year", "yoy", "y-o-y", "annual growth", "yoy growth"]
            ),
            
            # 2. Market Share & Contribution
            IntentRule(
                "MARKET_SHARE", 0.98,
                ["market share", "share of market", "share percentage"]
            ),
            IntentRule(
                "CONTRIBUTION", 0.98,
                ["contribution", "contributes", "contributing", "share in sales", "share of sales"]
            ),
            
            # 3. Growth & Decline
            IntentRule(
                "GROWTH", 0.97,
                ["growth", "grew", "percentage increase", "growing", "gain", "gains", "increase in sales"]
            ),
            IntentRule(
                "DECLINE", 0.97,
                ["decline", "declined", "dropped", "drop", "decrease", "loss", "losses", "falling", "reduced", "negative growth"]
            ),
            
            # 4. Top N / Bottom N / Ranking
            IntentRule(
                "TOP_N", 0.96,
                ["most sold", "highest sales", "largest sales", "best sales", "biggest sales", "leading product", "leading company"],
                [r"\btop\b", r"\bhighest\b", r"\blargest\b", r"\bbest\b", r"\bmost\b", r"\bbiggest\b", r"\bleading\b"]
            ),
            IntentRule(
                "BOTTOM_N", 0.96,
                ["lowest sales", "least sales", "smallest sales", "worst sales", "fewest sales"],
                [r"\bbottom\b", r"\blowest\b", r"\bleast\b", r"\bsmallest\b", r"\bworst\b", r"\bfewest\b"]
            ),
            IntentRule(
                "RANKING", 0.98,
                ["ranking", "rank", "ranks", "order by", "ordered list", "position"]
            ),
            
            # 5. Compare / Trend
            IntentRule(
                "COMPARE", 0.98,
                ["compare", "comparison", "compared", "difference between", "sales difference", "difference in sales", "sales variance"],
                [r"\bvs\b", r"\bversus\b", r"\bagainst\b"]
            ),
            IntentRule(
                "TREND", 0.94,
                ["trend", "trends", "over time", "time series", "trajectory", "progression", "historical", "by month", "by year"]
            ),
            
            # 6. Aggregates (Average / Maximum / Minimum)
            IntentRule(
                "AVERAGE", 0.93,
                ["average", "avg", "mean", "monthly average", "yearly average"]
            ),
            IntentRule(
                "MAXIMUM", 0.93,
                ["maximum", "max", "peak", "highest value"]
            ),
            IntentRule(
                "MINIMUM", 0.93,
                ["minimum", "min", "trough", "lowest value"]
            ),
            
            # 7. Total Sales
            IntentRule(
                "TOTAL_SALES", 0.85,
                ["total sales", "actual sales", "volume", "sum of sales", "revenue", "turnover", "sold", "sales"]
            )
        ]

    def _normalize(self, text: str) -> str:
        # Lowercase, clean punctuation, collapse whitespace
        normalized = str(text or "").strip().lower()
        normalized = re.sub(r"[^a-z0-9\s\-\/]", " ", normalized)
        return " " + re.sub(r"\s+", " ", normalized).strip() + " "

    def classify(self, question: str) -> Dict[str, Union[str, float]]:
        norm_text = self._normalize(question)
        
        # Check rules in order of priority
        for rule in self.rules:
            if rule.matches(norm_text):
                return {
                    "intent": rule.intent,
                    "confidence": rule.confidence
                }
        
        # Fallback default
        return {
            "intent": "TOTAL_SALES",
            "confidence": 0.50
        }
