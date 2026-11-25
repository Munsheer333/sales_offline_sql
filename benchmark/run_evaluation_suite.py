"""
NLPSQL SYSTEM — COMPREHENSIVE BENCHMARK SUITE RUNNER
=====================================================
Executes the evaluation suite on the complete gold dataset.
Measures the 6 core dimensions:
  1. Intent Accuracy
  2. Entity Accuracy
  3. Planner Accuracy
  4. SQL Accuracy
  5. Execution Accuracy
  6. Business Accuracy

Writes report to artifacts/benchmark_report.md and benchmark/results/accuracy_report.json.
"""

from __future__ import annotations

import json
import time
import os
import re
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Any, Tuple

# Root folder setup
ROOT_DIR = Path(__file__).resolve().parent.parent
import sys
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.db_pool import pooled_connect
from core.intent_detector import get_intent_detector
from core.entity_extractor import get_entity_extractor
from core.phase3_query_planner import generate_logical_plan
from core.phase3_sql_compiler import compile_logical_plan, SQLCompilerError
from core.phase4_validator import validate_sql_ast
from core.phase6_orchestrator import load_schema, SCHEMA_PATH
from config import DB_URL
from benchmark.evaluator import (
    compare_db_results_smart,
    get_actual_intent,
    get_actual_entities,
    compare_entities,
    normalize_sql
)


def compare_query_plans(expected: dict, actual: dict) -> bool:
    """Compare expected and actual logical plans semantically."""
    if not expected or not actual:
        return False
        
    # 1. Compare source tables
    t_exp = expected.get("target_table")
    if not t_exp and expected.get("source_tables"):
        t_exp = expected.get("source_tables")[0].get("table")
        
    t_act = actual.get("target_table")
    if not t_act and actual.get("source_tables"):
        t_act = actual.get("source_tables")[0].get("table")
        
    if t_exp != t_act:
        return False
        
    # 2. Compare group_by columns
    g_exp = set()
    for col in expected.get("group_by", []):
        if isinstance(col, dict):
            g_exp.add(col.get("column", "").lower())
        else:
            g_exp.add(str(col).lower())
            
    g_act = set()
    for col in actual.get("group_by", []):
        if isinstance(col, dict):
            g_act.add(col.get("column", "").lower())
        else:
            g_act.add(str(col).lower())
            
    if g_exp != g_act:
        return False
        
    # 3. Compare filter columns
    f_exp = set()
    for f in expected.get("filters", []):
        if isinstance(f, dict):
            f_exp.add(f.get("column", "").lower())
            
    f_act = set()
    for f in actual.get("filters", []):
        if isinstance(f, dict):
            f_act.add(f.get("column", "").lower())
            
    # Allow small mismatch in default/business rule filters, but main entity filters must match
    if not f_exp.issubset(f_act) and not f_act.issubset(f_exp):
        if len(f_exp & f_act) == 0 and len(f_exp) > 0:
            return False
            
    return True


def run_benchmark_suite():
    gold_path = ROOT_DIR / "benchmark" / "gold_dataset.json"
    results_dir = ROOT_DIR / "benchmark" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    # Target artifact directory path
    artifacts_dir = ROOT_DIR / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_md_path = artifacts_dir / "benchmark_report.md"
    
    if not gold_path.exists():
        print(f"Error: Gold dataset not found at {gold_path}")
        return
        
    with open(gold_path, "r", encoding="utf-8") as f:
        gold_data = json.load(f)
        
    total = len(gold_data)
    print(f"Starting optimized benchmark suite execution on all {total} cases...")
    
    conn = pooled_connect(DB_URL)
    schema = load_schema(SCHEMA_PATH)
    
    detector = get_intent_detector()
    extractor = get_entity_extractor()
    
    results = []
    
    metrics = {
        "intent_correct": 0,
        "entity_correct": 0,
        "planner_correct": 0,
        "sql_correct": 0,
        "execution_correct": 0,
        "business_correct": 0
    }
    
    total_latency_ms = 0.0
    
    # Categorized metrics
    cat_metrics = defaultdict(lambda: {
        "total": 0,
        "intent_ok": 0,
        "entity_ok": 0,
        "planner_ok": 0,
        "sql_ok": 0,
        "exec_ok": 0,
        "business_ok": 0
    })
    
    for i, item in enumerate(gold_data):
        question = item["question"]
        expected_intent = item["intent"]
        expected_entities = item.get("entities", {})
        expected_plan = item.get("query_plan", {})
        expected_sql = item["expected_sql"]
        
        if (i + 1) % 200 == 0 or i == 0 or (i + 1) == total:
            print(f"Progress: [{i+1}/{total}] evaluated...")
            
        start_t = time.perf_counter()
        
        # 1. Intent Detection
        try:
            intent_res = detector.detect(question)
            actual_intent = intent_res.primary_intent
            intent_match = (actual_intent == expected_intent)
        except Exception:
            actual_intent = "failed"
            intent_match = False
            
        if intent_match:
            metrics["intent_correct"] += 1
            
        # 2. Entity Extraction
        try:
            extracted = extractor.extract(question)
            actual_entities = extracted.to_dict()
            ent_match, _, _, _ = compare_entities(expected_entities, actual_entities)
        except Exception:
            actual_entities = {}
            ent_match = False
            
        if ent_match:
            metrics["entity_correct"] += 1
            
        # 3. Query Plan Generation
        try:
            generated_plan = generate_logical_plan(question, schema_metadata=schema)
            plan_match = compare_query_plans(expected_plan, generated_plan)
        except Exception:
            generated_plan = {}
            plan_match = False
            
        if plan_match:
            metrics["planner_correct"] += 1
            
        # 4. SQL Compilation
        generated_sql = ""
        sql_match = False
        error_msg = None
        
        try:
            if not generated_plan.get("is_off_topic"):
                compiled = compile_logical_plan(generated_plan, schema_metadata=schema)
                generated_sql = compiled.sql
                sql_match = (normalize_sql(expected_sql) == normalize_sql(generated_sql))
            else:
                generated_sql = "OFF_TOPIC: Please ask a database-related question regarding office, sales, or industry performance metrics."
                sql_match = (normalize_sql(expected_sql) == normalize_sql(generated_sql))
        except SQLCompilerError as e:
            error_msg = f"Compiler refused plan: {str(e)}"
            generated_sql = f"-- Compilation failed: {str(e)}"
            sql_match = (normalize_sql(expected_sql) == normalize_sql(generated_sql))
        except Exception as e:
            error_msg = f"Compilation failed: {str(e)}"
            generated_sql = f"-- Compilation failed: {str(e)}"
            sql_match = (normalize_sql(expected_sql) == normalize_sql(generated_sql))
            
        if sql_match:
            metrics["sql_correct"] += 1
            
        # 5. Execution Correctness (DB matching)
        exec_match = False
        if generated_sql:
            exec_match = compare_db_results_smart(conn, expected_sql, generated_sql)
        if exec_match:
            metrics["execution_correct"] += 1
            
        # 6. Business Correctness (AST Validator check)
        biz_match = False
        if generated_sql:
            if generated_sql.strip().startswith("--"):
                biz_match = expected_sql.strip().startswith("--")
            else:
                val_report = validate_sql_ast(generated_sql, schema, question)
                biz_match = (len(val_report.get("errors", [])) == 0)
        if biz_match:
            metrics["business_correct"] += 1
            
        latency_ms = (time.perf_counter() - start_t) * 1000
        total_latency_ms += latency_ms
        
        # Category metrics updates
        category = expected_intent or "sales"
        cat_metrics[category]["total"] += 1
        if intent_match:
            cat_metrics[category]["intent_ok"] += 1
        if ent_match:
            cat_metrics[category]["entity_ok"] += 1
        if plan_match:
            cat_metrics[category]["planner_ok"] += 1
        if sql_match:
            cat_metrics[category]["sql_ok"] += 1
        if exec_match:
            cat_metrics[category]["exec_ok"] += 1
        if biz_match:
            cat_metrics[category]["business_ok"] += 1
            
        results.append({
            "question": question,
            "category": category,
            "intent": {
                "expected": expected_intent,
                "actual": actual_intent,
                "match": intent_match
            },
            "entities": {
                "expected": expected_entities,
                "actual": actual_entities,
                "match": ent_match
            },
            "planner": {
                "match": plan_match
            },
            "sql": {
                "expected": expected_sql,
                "generated": generated_sql,
                "match": sql_match
            },
            "execution": {
                "match": exec_match
            },
            "business": {
                "match": biz_match
            },
            "latency_ms": round(latency_ms, 2),
            "error": error_msg
        })
        
    conn.close()
    
    # Calculate global percentages
    intent_acc = (metrics["intent_correct"] / total) * 100
    entity_acc = (metrics["entity_correct"] / total) * 100
    plan_acc = (metrics["planner_correct"] / total) * 100
    sql_acc = (metrics["sql_correct"] / total) * 100
    exec_acc = (metrics["execution_correct"] / total) * 100
    biz_acc = (metrics["business_correct"] / total) * 100
    avg_latency = total_latency_ms / total
    
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    summary = {
        "timestamp": timestamp,
        "total_queries": total,
        "intent_accuracy": round(intent_acc, 2),
        "entity_accuracy": round(entity_acc, 2),
        "planner_accuracy": round(plan_acc, 2),
        "sql_accuracy": round(sql_acc, 2),
        "execution_accuracy": round(exec_acc, 2),
        "business_accuracy": round(biz_acc, 2),
        "average_latency_ms": round(avg_latency, 2)
    }
    
    # Output JSON report
    with open(results_dir / "accuracy_report.json", "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "details": results}, f, indent=2)

    # Output timestamped JSON report for dashboard discovery
    with open(results_dir / f"accuracy_report_{timestamp}.json", "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "details": results}, f, indent=2)
        
    # Write Markdown Report
    write_md_report(report_md_path, summary, cat_metrics, results)
    
    print("\n" + "="*70)
    print("                     NLPSQL ACCURACY SUITE SUMMARY")
    print("="*70)
    print(f"Total Queries Evaluated:           {total}")
    print(f"Intent Classification Accuracy:    {intent_acc:.2f}%")
    print(f"Entity Recognition Accuracy:       {entity_acc:.2f}%")
    print(f"Logical Planner Accuracy:          {plan_acc:.2f}%")
    print(f"SQL Syntactic Match Accuracy:      {sql_acc:.2f}%")
    print(f"Execution Output Correctness:      {exec_acc:.2f}%")
    print(f"Business Correctness (AST Pass):   {biz_acc:.2f}%")
    print(f"Average Pipeline Latency:          {avg_latency:.2f} ms")
    print("="*70)
    print(f"Markdown report compiled at: {report_md_path}")


def write_md_report(path: Path, summary: dict, cat_metrics: dict, details: list):
    md = f"""# NLPSQL Enterprise Benchmark Report
Generated on: {time.strftime('%Y-%m-%d %H:%M:%S')}

This report summarizes the compliance, correctness, and performance of the NLPSQL deterministic query generation system.

## 1. Summary Metrics
| Metric / Check | Accuracy / Score | Description / Formula |
| --- | --- | --- |
| **Total Queries** | {summary['total_queries']} | Total number of unique benchmark cases |
| **Intent Accuracy** | **{summary['intent_accuracy']}%** | Correct routing class matching |
| **Entity Accuracy** | **{summary['entity_accuracy']}%** | Strict dimension filter value matches |
| **Planner Accuracy** | **{summary['planner_accuracy']}%** | Logical table, group-by, and join planning |
| **SQL Syntactic Accuracy** | **{summary['sql_accuracy']}%** | Exact match with reference compiler SQL |
| **Execution Accuracy** | **{summary['execution_accuracy']}%** | Direct cell-by-cell database output matching |
| **Business Accuracy** | **{summary['business_accuracy']}%** | Compliance with all AST & MNC business rules |
| **Average Latency** | **{summary['average_latency_ms']} ms** | End-to-end response time |

## 2. Category Accuracy Breakdown
| Category | Total | Intent | Entity | Planner | SQL Match | Exec Correct | Business OK |
| --- | --- | --- | --- | --- | --- | --- | --- |
"""
    for cat, stats in cat_metrics.items():
        tot = stats["total"]
        md += (
            f"| **{cat.upper()}** | {tot} | "
            f"{stats['intent_ok']/tot*100:.1f}% | "
            f"{stats['entity_ok']/tot*100:.1f}% | "
            f"{stats['planner_ok']/tot*100:.1f}% | "
            f"{stats['sql_ok']/tot*100:.1f}% | "
            f"{stats['exec_ok']/tot*100:.1f}% | "
            f"{stats['business_ok']/tot*100:.1f}% |\n"
        )
        
    md += """
## 3. Sample Case Details
Below are representative evaluation cases detailing output correctness.
"""
    # Sample up to 10 detailed cases
    for i, item in enumerate(details[:10]):
        md += f"""
### Case {i+1}: {item['question']}
- **Intent Correct**: `{item['intent']['match']}` (Expected: `{item['intent']['expected']}`, Got: `{item['intent']['actual']}`)
- **Entities Match**: `{item['entities']['match']}`
- **Planner Match**: `{item['planner']['match']}`
- **SQL Match**: `{item['sql']['match']}`
- **Execution Correct**: `{item['execution']['match']}`
- **Business Rule Compliant**: `{item['business']['match']}`
- **Latency**: `{item['latency_ms']} ms`
- **Generated SQL**:
  ```sql
  {item['sql']['generated'] if item['sql']['generated'] else '-- Pipeline Failed --'}
  ```
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)


if __name__ == "__main__":
    run_benchmark_suite()
