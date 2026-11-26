import re
from typing import Dict, List, Optional
from core.query_normalizer import COMPANY_NAMES, PRODUCT_NAMES, SBU_VALUES, ZONES

_SESSION_HISTORY: Dict[str, List[str]] = {}

def get_session_history(session_id: str) -> List[str]:
    return _SESSION_HISTORY.setdefault(session_id, [])

def clear_session_context(session_id: str) -> None:
    if session_id in _SESSION_HISTORY:
        _SESSION_HISTORY[session_id] = []

def add_to_session_history(session_id: str, question: str) -> None:
    history = get_session_history(session_id)
    history.append(question)
    if len(history) > 10:
        history.pop(0)

def resolve_conversational_query(question: str, session_id: str) -> str:
    if not session_id:
        return question

    history = get_session_history(session_id)
    if not history:
        add_to_session_history(session_id, question)
        return question

    prev_query = history[-1]
    q_clean = question.strip().lower()
    prev_clean = prev_query.strip().lower()

    # 0. Check if this is an independent, complete new query.
    has_dual_operands = bool(re.search(r'\b(?:compare\s+.+?\s+(?:and|vs|versus|with)\s+.+|.+?\s+(?:vs|versus)\s+.+|difference\s+between\s+.+?\s+and\s+.+)\b', q_clean))
    is_elliptical_comparison = any(q_clean.startswith(p) for p in ["compare with ", "compare against ", "compare to ", "vs ", "versus ", "against "])

    starts_with_prefix = any(q_clean.startswith(p) for p in ["what is", "what are", "show ", "get ", "find ", "list ", "run ", "calculate ", "compare ", "rank "])
    is_followup_phrase = any(q_clean.startswith(p) for p in ["what about", "how about", "only "]) or is_elliptical_comparison
    from core.query_normalizer import detect_incomplete_question
    is_incomplete = detect_incomplete_question(question) is not None

    # Check if the query is a growth follow-up
    is_growth_query = any(term in q_clean for term in ["growth", "difference", "yoy", "year-on-year", "year over year"])
    has_any_entity = (
        any(re.search(rf"\b{c.lower()}\b", q_clean) for c in COMPANY_NAMES) or
        any(re.search(rf"\b{z.lower()}\b", q_clean) for z in ZONES) or
        any(re.search(rf"\b{p.lower()}\b", q_clean) for p in PRODUCT_NAMES) or
        any(re.search(rf"\b{s.lower()}\b", q_clean) for s in SBU_VALUES)
    )
    is_growth_followup = is_growth_query and (not has_any_entity or q_clean.startswith("show ") or q_clean.startswith("calculate "))

    if (has_dual_operands and not is_elliptical_comparison) or (starts_with_prefix and not is_followup_phrase and not is_incomplete and not is_growth_followup):
        clear_session_context(session_id)
        add_to_session_history(session_id, question)
        return question

    if is_growth_followup:
        resolved = prev_query
        if not any(term in prev_clean for term in ["growth", "difference", "yoy", "year-on-year", "year over year"]):
            resolved = f"{resolved} {question}"
        resolved = re.sub(r"\s+", " ", resolved).strip()
        add_to_session_history(session_id, resolved)
        return resolved

    # 1. Check if the current query is a comparison instruction (e.g. "compare with BPCL" or "vs BPCL")
    is_comparison = any(term in q_clean for term in ["compare", "vs", "versus", "against"])
    
    # Extract companies
    curr_companies = [c for c in COMPANY_NAMES if re.search(rf"\b{c.lower()}\b", q_clean)]
    prev_companies = [c for c in COMPANY_NAMES if re.search(rf"\b{c.lower()}\b", prev_clean)]

    if is_comparison and curr_companies and prev_companies:
        new_company = curr_companies[0]
        old_company = prev_companies[0]
        
        # We want to rewrite the previous query to compare the old company with the new company
        # Remove old company name from previous query to generalize it
        rest_of_prev = re.sub(rf"\b{old_company}\b", "", prev_query, flags=re.IGNORECASE).strip()
        # Clean up double spaces
        rest_of_prev = re.sub(r"\s+", " ", rest_of_prev)
        
        # Construct the comparison query
        resolved = f"Compare {old_company} with {new_company} {rest_of_prev}"
        resolved = re.sub(r"\s+", " ", resolved).strip()
        add_to_session_history(session_id, resolved)
        return resolved

    # 2. Check if current query is a filter refinement (starts with refinement keywords or contains dimensions)
    is_refinement = (
        q_clean.startswith("only ") or
        q_clean.startswith("in ") or
        q_clean.startswith("for ") or
        q_clean.startswith("with ") or
        any(re.search(rf"\b{re.escape(z.lower())}\b", q_clean) for z in ZONES) or
        any(re.search(rf"\b{re.escape(p.lower())}\b", q_clean) for p in PRODUCT_NAMES) or
        any(re.search(rf"\b{re.escape(s.lower())}\b", q_clean) for s in SBU_VALUES) or
        any(re.search(rf"\b{y}", q_clean) for y in ["fy", "202"])
    )

    if is_refinement:
        resolved = prev_query
        
        # If there is a replacement of zone (e.g. prev had North, new has South)
        curr_zones = [z for z in ZONES if re.search(rf"\b{z.lower()}\b", q_clean)]
        prev_zones = [z for z in ZONES if re.search(rf"\b{z.lower()}\b", prev_clean)]
        if curr_zones and prev_zones:
            resolved = re.sub(rf"\b{prev_zones[0]}\b", curr_zones[0], resolved, flags=re.IGNORECASE)
            # Remove "only" or other prefix if present in the rest of current query to avoid duplicate zone words
            remaining_q = re.sub(rf"\b{curr_zones[0].lower()}\b", "", q_clean).strip()
            remaining_q = re.sub(r"\b(only|in|for|with)\b", "", remaining_q).strip()
            if remaining_q:
                resolved = f"{resolved} {remaining_q}"
        else:
            # Just append the new filter
            resolved = f"{resolved} {question}"

        resolved = re.sub(r"\s+", " ", resolved).strip()
        add_to_session_history(session_id, resolved)
        return resolved

    # 3. Default fallback: check if the new query is complete.
    # If complete, treat as a new topic. If incomplete, merge with previous.
    if is_incomplete:
        resolved = f"{prev_query} {question}"
        resolved = re.sub(r"\s+", " ", resolved).strip()
        add_to_session_history(session_id, resolved)
        return resolved
    else:
        # Complete new query - start a new history
        clear_session_context(session_id)
        add_to_session_history(session_id, question)
        return question
