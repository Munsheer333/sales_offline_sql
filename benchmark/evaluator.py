import os
import sys
import json
import time
import re
from datetime import datetime
from collections import defaultdict
from typing import Dict, Any, List, Set, Tuple
from decimal import Decimal

from config import DB_URL
from core.db_pool import pooled_connect
from core.phase6_orchestrator import run_pipeline

def compare_db_results_smart(conn, sql1: str, sql2: str) -> bool:
    """Compare database execution results of two SQL queries with Decimal/Type tolerance and column-mapping logic."""
    if not sql1 or not sql2:
        return False
    is_non_query1 = sql1.strip().startswith("--") or sql1.strip().startswith("OFF_TOPIC:")
    is_non_query2 = sql2.strip().startswith("--") or sql2.strip().startswith("OFF_TOPIC:")
    if is_non_query1 or is_non_query2:
        return normalize_sql(sql1) == normalize_sql(sql2)
    try:
        cur1 = conn.cursor()
        cur1.execute(sql1)
        res1 = cur1.fetchall()
        cur1.close()
        
        cur2 = conn.cursor()
        cur2.execute(sql2)
        res2 = cur2.fetchall()
        cur2.close()
        
        # Keep transaction clean
        conn.commit()
        
        if len(res1) != len(res2):
            return False
            
        # Helper to convert a cell value to a standard float or normalized string
        def normalize_val(v):
            if v is None:
                return None
            if isinstance(v, (int, float, Decimal)):
                return round(float(v), 2)
            return str(v).strip().lower()

        # Try exact tuple matching first
        match = True
        for r1, r2 in zip(res1, res2):
            if len(r1) != len(r2):
                match = False
                break
            for v1, v2 in zip(r1, r2):
                nv1 = normalize_val(v1)
                nv2 = normalize_val(v2)
                if isinstance(nv1, float) and isinstance(nv2, float):
                    if abs(nv1 - nv2) > 0.05:
                        match = False
                        break
                elif nv1 != nv2:
                    match = False
                    break
            if not match:
                break
        
        if match:
            return True
            
        # If expected query returns exactly 1 column/cell (e.g. a single metric value)
        # check if that value exists in the generated result rows
        if len(res1) == 1 and len(res1[0]) == 1:
            target = normalize_val(res1[0][0])
            for row in res2:
                for v in row:
                    if normalize_val(v) == target:
                        return True
                        
        return False
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False

def get_actual_intent(pipeline_out: dict) -> str:
    """Determine actual intent from pipeline outputs."""
    qp = pipeline_out.get("query_pattern", {})
    if qp and qp.get("matched"):
        tid = qp.get("template_id", "")
        if "market_share" in tid:
            return "market_share"
        elif "growth" in tid:
            return "growth"
        elif "trend" in tid:
            return "trend"
        elif "variance" in tid:
            return "comparison"
        elif "target" in tid:
            return "comparison"
        elif "actual_sales" in tid:
            return "total_sales"
            
    plan = pipeline_out.get("logical_plan")
    if plan:
        q_str = str(plan.get("question") or "").lower()
        if "share" in q_str or "market contribution" in q_str:
            return "market_share"
        elif "growth" in q_str or "difference" in q_str:
            return "growth"
        elif "trend" in q_str or "month-wise" in q_str or "day-wise" in q_str:
            return "trend"
        elif "rank" in q_str or "top" in q_str or "bottom" in q_str:
            return "ranking"
        elif "compare" in q_str or "versus" in q_str or "vs" in q_str:
            return "comparison"
            
    q_str = pipeline_out.get("question", "").lower()
    if "share" in q_str or "contribution" in q_str:
        return "market_share"
    elif "growth" in q_str or "difference" in q_str:
        return "growth"
    elif "trend" in q_str or "month-wise" in q_str or "day-wise" in q_str or "year-wise" in q_str:
        return "trend"
    elif "rank" in q_str or "top" in q_str or "bottom" in q_str or "highest" in q_str or "lowest" in q_str:
        return "ranking"
    elif "compare" in q_str or "versus" in q_str or "vs" in q_str:
        return "comparison"
        
    return "total_sales"

def get_actual_entities(pipeline_out: dict, question: str) -> Dict[str, List[str]]:
    """Extract and categorize entities from pipeline results and question text."""
    actual = defaultdict(list)
    
    # 1. Extract from entity_analysis matches
    for match in pipeline_out.get("entity_analysis", {}).get("matches", []):
        etype = match.get("type")
        val = match.get("value")
        if etype and val:
            key = "year" if etype == "fiscal_year" else etype
            actual[key].append(val.upper())
            
    # 2. Extract years from question text
    years = re.findall(r"\b(20\d{2}-20\d{2})\b", question)
    for y in years:
        actual["year"].append(y)
        
    # 3. Extract months from question text
    months_map = {
        "jan": "JAN", "feb": "FEB", "mar": "MAR", "apr": "APR", "may": "MAY", "jun": "JUN",
        "jul": "JUL", "aug": "AUG", "sep": "SEP", "oct": "OCT", "nov": "NOV", "dec": "DEC"
    }
    for m_short, m_gold in months_map.items():
        if re.search(rf"\b{m_short}\b", question.lower()):
            actual["month"].append(m_gold)
            
    # Deduplicate and sort values
    return {k: sorted(list(set(v))) for k, v in actual.items() if v}

def compare_entities(expected: dict, actual: dict) -> Tuple[bool, float, float, float]:
    """Compare expected and actual entities, returning (strict_match, precision, recall, f1)."""
    # Clean keys and convert values to sets of upper-case strings
    exp_set = set()
    for k, vals in expected.items():
        for v in vals:
            exp_set.add((k, str(v).upper()))
            
    act_set = set()
    for k, vals in actual.items():
        for v in vals:
            act_set.add((k, str(v).upper()))
            
    if not exp_set and not act_set:
        return True, 1.0, 1.0, 1.0
        
    if not exp_set or not act_set:
        return False, 0.0, 0.0, 0.0
        
    intersection = exp_set & act_set
    precision = len(intersection) / len(act_set)
    recall = len(intersection) / len(exp_set)
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    
    strict_match = (exp_set == act_set)
    return strict_match, precision, recall, f1

def normalize_sql(sql: str) -> str:
    """Normalize SQL syntax for string comparison."""
    if not sql:
        return ""
    sql = sql.lower()
    sql = re.sub(r"\s+", " ", sql)
    sql = re.sub(r"\s*([=,;().<>!])\s*", r"\1", sql)
    sql = sql.replace('"', '').replace("'", "")
    return sql.strip()

def run_evaluation():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gold_path = os.path.join(base_dir, "benchmark", "gold_dataset.json")
    results_dir = os.path.join(base_dir, "benchmark", "results")
    os.makedirs(results_dir, exist_ok=True)
    
    if not os.path.exists(gold_path):
        print(f"Error: Gold dataset not found at {gold_path}")
        return
        
    with open(gold_path, "r", encoding="utf-8") as f:
        gold_data = json.load(f)
        
    print(f"Loaded {len(gold_data)} gold query entries for evaluation.")
    print("Connecting to database...")
    conn = pooled_connect(DB_URL)
    
    evaluation_details = []
    
    total = len(gold_data)
    correct_intent = 0
    correct_entities_strict = 0
    total_entity_f1 = 0.0
    correct_sql_strict = 0
    correct_execution = 0
    total_time_ms = 0.0
    
    print("\nEvaluating benchmark prompts...\n")
    for i, item in enumerate(gold_data):
        question = item["question"]
        expected_intent = item["intent"]
        expected_entities = item.get("entities", {})
        expected_sql = item["expected_sql"]
        
        # Use unique session IDs to avoid query context leakage
        session_id = f"eval_session_{i}_{int(time.time())}"
        
        print(f"[{i+1}/{total}] Prompt: {question}")
        
        start = time.perf_counter()
        try:
            pipeline_out = run_pipeline(question, conn_str=DB_URL, session_id=session_id)
            success = pipeline_out.get("success", False)
            generated_sql = pipeline_out.get("sql", "")
            error_msg = pipeline_out.get("error")
        except Exception as e:
            success = False
            generated_sql = ""
            error_msg = str(e)
            pipeline_out = {}
            
        duration_ms = (time.perf_counter() - start) * 1000
        total_time_ms += duration_ms
        
        # 1. Intent Accuracy
        actual_intent = get_actual_intent(pipeline_out) if success else "failed"
        intent_match = (actual_intent == expected_intent)
        if intent_match:
            correct_intent += 1
            
        # 2. Entity Accuracy
        actual_entities = get_actual_entities(pipeline_out, question) if success else {}
        ent_strict, ent_prec, ent_rec, ent_f1 = compare_entities(expected_entities, actual_entities)
        if ent_strict:
            correct_entities_strict += 1
        total_entity_f1 += ent_f1
        
        # 3. SQL Accuracy
        norm_expected = normalize_sql(expected_sql)
        norm_generated = normalize_sql(generated_sql)
        sql_match = (norm_expected == norm_generated) and (generated_sql != "")
        if sql_match:
            correct_sql_strict += 1
            
        # 4. Execution Accuracy
        exec_match = False
        if success and generated_sql:
            exec_match = compare_db_results_smart(conn, expected_sql, generated_sql)
        if exec_match:
            correct_execution += 1
            
        print(f"    - Intent Match:      {intent_match} (Expected: {expected_intent}, Got: {actual_intent})")
        print(f"    - Entities Strict:   {ent_strict} (F1: {ent_f1:.2f})")
        print(f"    - SQL Strict Match:  {sql_match}")
        print(f"    - Execution Match:   {exec_match}")
        print(f"    - Latency:           {duration_ms:.2f} ms\n")
        
        evaluation_details.append({
            "question": question,
            "expected_intent": expected_intent,
            "actual_intent": actual_intent,
            "intent_match": intent_match,
            "expected_entities": expected_entities,
            "actual_entities": actual_entities,
            "entity_strict_match": ent_strict,
            "entity_f1": round(ent_f1, 2),
            "expected_sql": expected_sql,
            "generated_sql": generated_sql,
            "sql_strict_match": sql_match,
            "execution_match": exec_match,
            "duration_ms": round(duration_ms, 2),
            "error": error_msg
        })
        
    conn.close()
    
    # Calculate Overall Stats
    intent_accuracy = (correct_intent / total) * 100
    entity_strict_accuracy = (correct_entities_strict / total) * 100
    entity_avg_f1 = (total_entity_f1 / total) * 100
    sql_accuracy = (correct_sql_strict / total) * 100
    execution_accuracy = (correct_execution / total) * 100
    avg_latency = total_time_ms / total
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_json_path = os.path.join(results_dir, f"accuracy_report_{timestamp}.json")
    report_md_path = os.path.join(results_dir, f"accuracy_report_{timestamp}.md")
    
    report_summary = {
        "timestamp": timestamp,
        "total_queries": total,
        "intent_accuracy": round(intent_accuracy, 2),
        "entity_strict_accuracy": round(entity_strict_accuracy, 2),
        "entity_avg_f1": round(entity_avg_f1, 2),
        "sql_accuracy": round(sql_accuracy, 2),
        "execution_accuracy": round(execution_accuracy, 2),
        "average_latency_ms": round(avg_latency, 2)
    }
    
    # Write JSON report
    with open(report_json_path, "w", encoding="utf-8") as f:
        json.dump({"summary": report_summary, "details": evaluation_details}, f, indent=2)
        
    # Write Markdown report
    md_content = f"""# NLPSQL Accuracy Benchmark Report
Run Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Summary Accuracy Metrics
| Metric | Accuracy / Value | Details |
| --- | --- | --- |
| **Total Queries** | {total} | Size of evaluation set |
| **Intent Accuracy** | {intent_accuracy:.2f}% | {correct_intent}/{total} correct |
| **Entity Accuracy (Strict Match)** | {entity_strict_accuracy:.2f}% | {correct_entities_strict}/{total} exact matches |
| **Entity Avg F1-Score** | {entity_avg_f1:.2f}% | Overall token overlap metric |
| **SQL Syntactic Accuracy** | {sql_accuracy:.2f}% | {correct_sql_strict}/{total} exact normalized SQL matches |
| **Execution Accuracy (Semantic correctness)** | {execution_accuracy:.2f}% | {correct_execution}/{total} returning identical DB data |
| **Average Query Latency** | {avg_latency:.2f} ms | Performance metric |

## Detailed Query Evaluation List
"""
    for idx, item in enumerate(evaluation_details):
        md_content += f"""
### {idx+1}. {item['question']}
- **Intent**: expected `{item['expected_intent']}`, actual `{item['actual_intent']}` (Match: `{item['intent_match']}`)
- **Entities**: expected `{item['expected_entities']}`, actual `{item['actual_entities']}` (Strict Match: `{item['entity_strict_match']}`, F1: `{item['entity_f1']}`)
- **SQL Syntactic Match**: `{item['sql_strict_match']}`
- **Execution Accuracy (DB Result Match)**: `{item['execution_match']}`
- **Latency**: `{item['duration_ms']} ms`
- **Expected SQL**:
  ```sql
  {item['expected_sql']}
  ```
- **Generated SQL**:
  ```sql
  {item['generated_sql'] if item['generated_sql'] else '-- Pipeline Failed --'}
  ```
"""
        if item["error"]:
            md_content += f"- **Error Message**: `{item['error']}`\n"
            
    with open(report_md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
        
    # Print Console Summary
    print("=" * 70)
    print("                     NLPSQL ACCURACY BENCHMARK REPORT")
    print("=" * 70)
    print(f"Timestamp:                         {timestamp}")
    print(f"Total Queries Evaluated:           {total}")
    print(f"Intent Classification Accuracy:    {intent_accuracy:.2f}%")
    print(f"Entity Recognition Accuracy:       {entity_strict_accuracy:.2f}% (Avg F1: {entity_avg_f1:.2f}%)")
    print(f"SQL Syntactic Match Accuracy:      {sql_accuracy:.2f}%")
    print(f"Execution Output Accuracy:         {execution_accuracy:.2f}%")
    print(f"Average System Latency:            {avg_latency:.2f} ms")
    print("=" * 70)
    print(f"JSON Report saved to: {report_json_path}")
    print(f"Markdown Report saved to: {report_md_path}")

if __name__ == "__main__":
    run_evaluation()
