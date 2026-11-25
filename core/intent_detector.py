"""
NLPSQL SYSTEM — INTENT DETECTOR
=================================
Detects the business intent of a natural language question and returns
a ranked list of (intent, confidence) pairs.

Supported intents:
  total_sales      — aggregate sum of sales
  actual_volume    — same as total_sales but focused on raw volume
  market_share     — percentage share of total market
  growth           — YoY or MoM percentage change
  sales_difference — absolute delta between two periods/entities
  comparison       — side-by-side values (not a delta, just compare)
  top_n            — highest N entities by a metric
  bottom_n         — lowest N entities by a metric
  trend            — time-series data (monthly/quarterly/yearly)

Usage:
    from core.intent_detector import IntentDetector
    detector = IntentDetector()

    result = detector.detect("Show monthly sales trend for HPCL")
    print(result.primary_intent)       # "trend"
    print(result.confidence)           # 0.96
    print(result.all_scores)           # {"trend": 0.96, "total_sales": 0.22, ...}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Intent definitions — each has keyword signals with weights
# Weights: 1.0 = strong exclusive signal, 0.5 = supporting signal
# ─────────────────────────────────────────────────────────────────────────────

_INTENT_SIGNALS: Dict[str, List[Tuple[str, float]]] = {

    "market_share": [
        (r"\bmarket\s*share\b",                1.0),
        (r"\bshare\s*of\s*market\b",           1.0),
        (r"\bmarket\s*contribution\b",          0.9),
        (r"\bcontribut(ion|ed|es|ing)\b",       0.6),
        (r"\bpercent(age)?\s*of\s*(total|market|industry)\b", 0.8),
        (r"\bshare\b",                          0.3),
    ],

    "growth": [
        (r"\bgrowth\b",                         1.0),
        (r"\bgrew\b",                            0.9),
        (r"\byoy\b",                             1.0),
        (r"\byear[\s\-]on[\s\-]year\b",         1.0),
        (r"\byear[\s\-]over[\s\-]year\b",       1.0),
        (r"\bmom\b",                             0.9),
        (r"\bmonth[\s\-]over[\s\-]month\b",     0.9),
        (r"\bmonth[\s\-]on[\s\-]month\b",       0.9),
        (r"\bqoq\b",                             0.9),
        (r"\bquarter[\s\-]over[\s\-]quarter\b", 0.9),
        (r"\bgrowth\s*rate\b",                  1.0),
        (r"\bincreas(e|ed|ing)\b",              0.5),
        (r"\bdeclin(e|ed|ing)\b",               0.6),
        (r"\bdecreas(e|ed|ing)\b",              0.5),
        (r"\bdropp(ed|ing)\b",                  0.5),
        (r"\bfell\b",                            0.5),
        (r"\bfallen\b",                          0.5),
        (r"\bnegative\s*growth\b",              0.9),
    ],

    "sales_difference": [
        (r"\bdifferenc(e|es)\b",                1.0),
        (r"\bdelta\b",                           0.9),
        (r"\bgap\b",                             0.7),
        (r"\bvarianc(e|es)\b",                  0.8),
        (r"\bmore\s*than\b",                    0.4),
        (r"\bless\s*than\b",                    0.4),
        (r"\babove\s*(average|mean)\b",         0.7),
        (r"\bbelow\s*(average|mean)\b",         0.7),
        (r"\bhow\s*much\s*(more|less|higher|lower)\b", 0.9),
    ],

    "comparison": [
        (r"\bcompar(e|ed|ison|ing)\b",          1.0),
        (r"\bvs\.?\b",                           0.9),
        (r"\bversus\b",                          0.9),
        (r"\bagainst\b",                         0.7),
        (r"\bvis-?a-?vis\b",                    0.8),
        (r"\brelative\s*to\b",                  0.7),
        (r"\bside\s*by\s*side\b",               0.8),
        (r"\band\s+(hpcl|iocl|bpcl|gail|shell|hmel|cpcl|mrpl|nrl|ril)\b", 0.6),
        (r"\b(hpcl|iocl|bpcl)\s+and\s+(hpcl|iocl|bpcl)\b", 0.8),
    ],

    "top_n": [
        (r"\btop[\s\-]?\d+\b",                  1.0),
        (r"\bfirst\s+\d+\b",                    0.9),
        (r"\bhighest\b",                         0.8),
        (r"\blargest\b",                         0.7),
        (r"\bmaximum\b",                         0.7),
        (r"\bmax\b",                             0.6),
        (r"\bbest\b",                            0.6),
        (r"\brank(ed|ing)?\b",                  0.7),
        (r"\bmost\b",                            0.5),
        (r"\bleading\b",                         0.6),
    ],

    "bottom_n": [
        (r"\bbottom[\s\-]?\d+\b",               1.0),
        (r"\blast\s+\d+\b",                     0.9),
        (r"\blowest\b",                          0.8),
        (r"\bsmallest\b",                        0.7),
        (r"\bminimum\b",                         0.7),
        (r"\bmin\b",                             0.6),
        (r"\bworst\b",                           0.7),
        (r"\bleast\b",                           0.7),
        (r"\bpoorest\b",                         0.6),
        (r"\bbottom\s+performing\b",            0.8),
    ],

    "trend": [
        (r"\btrend\b",                           1.0),
        (r"\bmonthly\b",                         0.9),
        (r"\bquarterly\b",                       0.9),
        (r"\byearly\b",                          0.8),
        (r"\bannual\b",                          0.7),
        (r"\bmonth[\s\-]by[\s\-]month\b",       0.9),
        (r"\bmonth\s*wise\b",                   0.9),
        (r"\bquarter\s*wise\b",                 0.9),
        (r"\bover\s+(the\s+)?months\b",         0.8),
        (r"\bover\s+(the\s+)?quarters\b",       0.8),
        (r"\bover\s+time\b",                    0.8),
        (r"\btime\s*series\b",                  0.9),
        (r"\bseasonalit(y|ies)\b",              0.7),
        (r"\bmovement\b",                        0.5),
        (r"\bpattern\b",                         0.4),
    ],

    "actual_volume": [
        (r"\bactual\b",                          0.9),
        (r"\bactual\s*volume\b",                1.0),
        (r"\bactual\s*sales\b",                 1.0),
        (r"\bnetweight\b",                       0.9),
        (r"\btmt\b",                             0.6),
        (r"\bmt\b",                              0.5),
        (r"\bofftake\b",                         0.8),
        (r"\bthroughput\b",                      0.8),
        (r"\bvolume\b",                          0.7),
        (r"\bunits\b",                           0.4),
    ],

    "total_sales": [
        (r"\btotal\s*(sales|volume|offtake)\b", 1.0),
        (r"\boverall\s*(sales|volume)\b",        0.9),
        (r"\baggregate\b",                       0.8),
        (r"\bsum\b",                             0.7),
        (r"\btotal\b",                           0.5),
        (r"\bsales\b",                           0.4),
        (r"\bsale\b",                            0.3),
        (r"\brevenue\b",                         0.4),
        (r"\bamount\b",                          0.3),
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Priority / disambiguation rules
# When multiple intents are close in score, these rules break ties.
# Higher priority = wins in a tie. (lower number = higher priority)
# ─────────────────────────────────────────────────────────────────────────────
_INTENT_PRIORITY: Dict[str, int] = {
    "market_share":     1,
    "growth":           2,
    "sales_difference": 3,
    "trend":            4,
    "top_n":            5,
    "bottom_n":         6,
    "comparison":       7,
    "total_sales":      8,
    "actual_volume":    9,
}

# Intents that are mutually exclusive with others
_MUTEX_PAIRS = [
    ("top_n", "bottom_n"),
    ("growth", "sales_difference"),
    ("market_share", "total_sales"),
    ("total_sales", "actual_volume"),
    ("sales_difference", "comparison"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IntentResult:
    primary_intent: str
    confidence: float                          # 0.0 – 1.0
    all_scores: Dict[str, float]               # intent → raw score (normalised)
    ranked: List[Tuple[str, float]]            # sorted list [(intent, score), ...]
    signals_fired: Dict[str, List[str]] = field(default_factory=dict)  # intent → patterns matched

    def is_confident(self, threshold: float = 0.45) -> bool:
        """True if the top intent confidence exceeds the threshold."""
        return self.confidence >= threshold

    def secondary_intent(self) -> Optional[str]:
        """Return the second-ranked intent if score > 0.20."""
        if len(self.ranked) > 1 and self.ranked[1][1] > 0.20:
            return self.ranked[1][0]
        return None

    def __repr__(self) -> str:
        return (
            f"IntentResult(primary={self.primary_intent!r}, "
            f"confidence={self.confidence:.2f}, "
            f"secondary={self.secondary_intent()!r})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Intent Detector
# ─────────────────────────────────────────────────────────────────────────────

class IntentDetector:
    """
    Detects business intent from a natural language question using
    weighted keyword signal matching.

    All signals are compiled to regex patterns at init time for speed.
    """

    def __init__(self, signals: Dict[str, List[Tuple[str, float]]] = None):
        signals = signals or _INTENT_SIGNALS
        # Pre-compile all patterns
        self._compiled: Dict[str, List[Tuple[re.Pattern, float, str]]] = {}
        for intent, patterns in signals.items():
            self._compiled[intent] = [
                (re.compile(pattern, re.IGNORECASE), weight, pattern)
                for pattern, weight in patterns
            ]

    # ── Public API ────────────────────────────────────────────────────────────

    def detect(self, question: str) -> IntentResult:
        """
        Detect the primary intent of a question.

        Args:
            question: Natural language query string.

        Returns:
            IntentResult with primary intent, confidence, and all scores.
        """
        q = question.strip()
        raw_scores: Dict[str, float] = {}
        signals_fired: Dict[str, List[str]] = {}

        # Score each intent
        for intent, patterns in self._compiled.items():
            score = 0.0
            fired = []
            seen_weights = set()
            for pattern, weight, pat_str in patterns:
                if pattern.search(q):
                    # Avoid double-counting very similar patterns
                    key = round(weight, 1)
                    if key not in seen_weights or weight == 1.0:
                        score += weight
                        seen_weights.add(key)
                        fired.append(pat_str)
            raw_scores[intent] = round(score, 4)
            signals_fired[intent] = fired

        # Apply mutual exclusion — suppress the lower-priority intent in each pair
        for intent_a, intent_b in _MUTEX_PAIRS:
            score_a = raw_scores.get(intent_a, 0)
            score_b = raw_scores.get(intent_b, 0)
            if score_a > 0 and score_b > 0:
                pri_a = _INTENT_PRIORITY.get(intent_a, 99)
                pri_b = _INTENT_PRIORITY.get(intent_b, 99)
                # Suppress the lower-priority (higher number) intent
                if pri_a < pri_b:
                    raw_scores[intent_b] *= 0.30
                else:
                    raw_scores[intent_a] *= 0.30

        # Normalise scores to [0, 1]
        max_score = max(raw_scores.values()) if raw_scores else 1.0
        if max_score == 0:
            max_score = 1.0
        normalised: Dict[str, float] = {
            k: round(min(v / max_score, 1.0), 4)
            for k, v in raw_scores.items()
        }

        # Rank intents: primary sort by normalised score, secondary by priority
        ranked = sorted(
            normalised.items(),
            key=lambda kv: (-kv[1], _INTENT_PRIORITY.get(kv[0], 99)),
        )

        primary_intent = ranked[0][0] if ranked else "total_sales"
        confidence = ranked[0][1] if ranked else 0.0

        # Fallback: if all scores are 0 or very low, default to total_sales
        if confidence < 0.10:
            primary_intent = "total_sales"
            confidence = 0.10

        return IntentResult(
            primary_intent=primary_intent,
            confidence=round(confidence, 4),
            all_scores=normalised,
            ranked=ranked,
            signals_fired={k: v for k, v in signals_fired.items() if v},
        )

    def detect_batch(self, questions: List[str]) -> List[IntentResult]:
        """Detect intent for a list of questions."""
        return [self.detect(q) for q in questions]


# ── Module-level singleton ─────────────────────────────────────────────────────
from functools import lru_cache

@lru_cache(maxsize=1)
def get_intent_detector() -> IntentDetector:
    return IntentDetector()


# ── CLI smoke-test ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    detector = IntentDetector()

    test_questions = [
        # total_sales
        ("total_sales",      "Show total sales for HPCL in FY 2024-2025"),
        ("total_sales",      "What is the overall sales volume in Maharashtra?"),
        # actual_volume
        ("actual_volume",    "Show actual volume of HSD for BPCL"),
        ("actual_volume",    "What is the actual offtake for LPG?"),
        # market_share
        ("market_share",     "Show market share of each company"),
        ("market_share",     "What is HPCL market contribution in retail?"),
        # growth
        ("growth",           "Show YoY growth for IOCL"),
        ("growth",           "Which states show declining sales?"),
        ("growth",           "Month over month growth for BPCL in FY 2024-2025"),
        # sales_difference
        ("sales_difference", "What is the difference in sales between HPCL and BPCL?"),
        ("sales_difference", "How much more did IOCL sell than BPCL?"),
        # comparison
        ("comparison",       "Compare HPCL vs BPCL sales"),
        ("comparison",       "HPCL and IOCL sales comparison"),
        # top_n
        ("top_n",            "Top 5 states by sales"),
        ("top_n",            "Which company had the highest sales?"),
        # bottom_n
        ("bottom_n",         "Bottom 3 districts by sales"),
        ("bottom_n",         "Lowest performing zones in ECZ"),
        # trend
        ("trend",            "Show monthly sales trend for Lubes"),
        ("trend",            "Quarterly trend of HSD in FY 2024-2025"),
        ("trend",            "Month by month sales for HPCL"),
    ]

    all_correct = 0
    print(f"{'Question':<52} {'Expected':<18} {'Got':<18} {'Conf':>6}  {'OK'}")
    print("-" * 110)
    for expected, q in test_questions:
        result = detector.detect(q)
        ok = "✅" if result.primary_intent == expected else "❌"
        if result.primary_intent == expected:
            all_correct += 1
        q_short = q[:50] + ".." if len(q) > 50 else q
        print(f"  {q_short:<52} {expected:<18} {result.primary_intent:<18} {result.confidence:>6.2f}  {ok}")

    print(f"\nAccuracy: {all_correct}/{len(test_questions)} ({all_correct/len(test_questions)*100:.0f}%)")
