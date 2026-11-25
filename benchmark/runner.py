import os
import sys
import json
import time
from datetime import datetime
from collections import defaultdict
from typing import Dict, Any, List

from config import DB_URL
from core.db_pool import pooled_connect
from core.phase6_orchestrator import run_pipeline

def compare_db_results(conn, sql1: str, sql2: str) -> bool:
    """Compare database execution results of two SQL queries with type tolerance."""
    if not sql1 or not sql2:
        return False
    try:
        cur1 = conn.cursor()
        cur1.execute(sql1)
        res1 = cur1.fetchall()
        cols1 = [desc[0] for desc in cur1.description]
        cur1.close()
        
        cur2 = conn.cursor()
        cur2.execute(sql2)
        res2 = cur2.fetchall()
        cols2 = [desc[0] for desc in cur2.description]
        cur2.close()
        
        if len(cols1) != len(cols2):
            return False
        if len(res1) != len(res2):
            return False
            
        for r1, r2 in zip(res1, res2):
            if len(r1) != len(r2):
                return False
            for v1, v2 in zip(r1, r2):
                if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
                    if abs(float(v1) - float(v2)) > 1e-2:
                        return False
                else:
                    if str(v1) != str(v2):
                        return False
        return True
    except Exception:
        return False

def run_benchmark():
    # Setup directories
    base_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(base_dir, "templates")
    generated_dir = os.path.join(base_dir, "generated")
    results_dir = os.path.join(base_dir, "results")
    
    os.makedirs(templates_dir, exist_ok=True)
    os.makedirs(generated_dir, exist_ok=True)
    os.makedirs(results_dir, exist_ok=True)
    
    # Parse CLI arguments
    use_generated = "--all" in sys.argv or "--generated" in sys.argv
    use_real = "--real-world" in sys.argv or "--real" in sys.argv
    
    if use_real:
        prompts_path = os.path.join(base_dir, "real_world_prompts.json")
        if not os.path.exists(prompts_path):
            print(f"Real world prompts not found at {prompts_path}. Running importer first...")
            from benchmark.import_prompts import import_and_process_prompts
            import_and_process_prompts()
    elif use_generated:
        prompts_path = os.path.join(generated_dir, "generated_prompts.json")
        if not os.path.exists(prompts_path):
            print(f"Generated prompts not found at {prompts_path}. Running generator first...")
            from benchmark.generator import generate_prompts
            generate_prompts()
    else:
        prompts_path = os.path.join(templates_dir, "golden_queries.json")
        
    if not os.path.exists(prompts_path):
        print(f"Error: Prompts file not found at {prompts_path}")
        return
        
    with open(prompts_path, "r", encoding="utf-8") as f:
        queries = json.load(f)
        
    # If running real-world, we may want to limit to unique non-duplicates/non-paraphrases for faster run
    if use_real:
        original_count = len(queries)
        # Filter to non-duplicates/non-paraphrases, prioritize ones with gold SQL
        queries = [q for q in queries if not q.get("is_duplicate", False) and not q.get("is_paraphrase", False)]
        print(f"Filtered real-world prompts from {original_count} down to {len(queries)} unique structural questions.")
        
    print(f"Loaded {len(queries)} queries from {os.path.basename(prompts_path)}.")
    print("Connecting to database...")
    
    conn = pooled_connect(DB_URL)
    results = []
    
    total_queries = len(queries)
    successful_pipelines = 0
    sql_execution_matches = 0
    comparable_queries = 0
    total_duration_ms = 0.0
    
    # Category statistics
    category_stats = defaultdict(lambda: {
        "total": 0,
        "success": 0,
        "match": 0,
        "comparable": 0,
        "duration_ms": 0.0
    })
    
    print("\nStarting benchmark run...\n")
    for i, item in enumerate(queries):
        category = item.get("category") or item.get("intent") or "unclassified"
        question = item.get("question") or item.get("prompt")
        expected_sql = item.get("sql") or item.get("gold_sql")
        expected_complexity = item.get("complexity")
        
        print(f"[{i+1}/{total_queries}] [{category.upper()}] Query: {question}")
        
        start_time = time.perf_counter()
        try:
            pipeline_out = run_pipeline(question, conn_str=DB_URL)
            success = pipeline_out.get("success", False)
            generated_sql = pipeline_out.get("sql", "")
            error_msg = pipeline_out.get("error")
            complexity = pipeline_out.get("complexity")
            intent = pipeline_out.get("intent")
        except Exception as e:
            success = False
            generated_sql = ""
            error_msg = str(e)
            complexity = None
            intent = None
            
        duration_ms = (time.perf_counter() - start_time) * 1000
        total_duration_ms += duration_ms
        
        # Compare DB execution results
        exec_match = False
        is_comparable = expected_sql is not None
        if is_comparable:
            comparable_queries += 1
            if success and generated_sql:
                exec_match = compare_db_results(conn, expected_sql, generated_sql)
            
        if success:
            successful_pipelines += 1
        if exec_match:
            sql_execution_matches += 1
            
        # Update category statistics
        category_stats[category]["total"] += 1
        if success:
            category_stats[category]["success"] += 1
        if is_comparable:
            category_stats[category]["comparable"] += 1
            if exec_match:
                category_stats[category]["match"] += 1
        category_stats[category]["duration_ms"] += duration_ms
            
        result_item = {
            "category": category,
            "question": question,
            "expected_sql": expected_sql,
            "generated_sql": generated_sql,
            "pipeline_success": success,
            "execution_match": exec_match,
            "is_comparable": is_comparable,
            "expected_complexity": expected_complexity,
            "actual_complexity": complexity,
            "actual_intent": intent,
            "duration_ms": round(duration_ms, 2),
            "error": error_msg
        }
        results.append(result_item)
        print(f"    - Pipeline Success: {success}")
        if is_comparable:
            print(f"    - Execution Match: {exec_match}")
        else:
            print(f"    - Execution Match: N/A (No reference SQL)")
        print(f"    - Duration: {duration_ms:.2f} ms\n")
        
    conn.close()
    
    # Compute summary stats
    avg_duration_ms = total_duration_ms / total_queries if total_queries > 0 else 0
    pipeline_success_rate = (successful_pipelines / total_queries) * 100 if total_queries > 0 else 0
    execution_match_rate = (sql_execution_matches / comparable_queries) * 100 if comparable_queries > 0 else 0.0
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Format category metrics
    category_summary = {}
    for cat, stats in category_stats.items():
        cat_total = stats["total"]
        cat_comparable = stats["comparable"]
        category_summary[cat] = {
            "total_queries": cat_total,
            "comparable_queries": cat_comparable,
            "pipeline_success_rate": round((stats["success"] / cat_total) * 100, 2) if cat_total > 0 else 0.0,
            "execution_match_rate": round((stats["match"] / cat_comparable) * 100, 2) if cat_comparable > 0 else 0.0,
            "average_duration_ms": round(stats["duration_ms"] / cat_total, 2) if cat_total > 0 else 0.0
        }
    
    summary = {
        "timestamp": timestamp,
        "total_queries": total_queries,
        "comparable_queries": comparable_queries,
        "successful_pipelines": successful_pipelines,
        "pipeline_success_rate": round(pipeline_success_rate, 2),
        "sql_execution_matches": sql_execution_matches,
        "execution_match_rate": round(execution_match_rate, 2),
        "average_duration_ms": round(avg_duration_ms, 2),
        "categories": category_summary
    }
    
    # Save raw generated queries
    run_prefix = "run_real_" if use_real else ("run_all_" if use_generated else "run_")
    gen_file = os.path.join(generated_dir, f"{run_prefix}{timestamp}.json")
    with open(gen_file, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "details": results}, f, indent=2)
        
    # Save summary report
    res_json_file = os.path.join(results_dir, f"report_{timestamp}.json")
    with open(res_json_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
        
    # Save markdown report
    res_md_file = os.path.join(results_dir, f"report_{timestamp}.md")
    with open(res_md_file, "w", encoding="utf-8") as f:
        f.write(generate_markdown_report(summary, results))
        
    # Print console report
    print_console_report(summary)
    print(f"Detailed run log saved to: {gen_file}")
    print(f"Report JSON saved to: {res_json_file}")
    print(f"Report Markdown saved to: {res_md_file}")

def generate_markdown_report(summary: Dict[str, Any], details: List[Dict[str, Any]]) -> str:
    md = f"""# NLPSQL Benchmark Report
Run Date: {summary['timestamp']}

## Summary Metrics
| Metric | Value |
| --- | --- |
| **Total Queries** | {summary['total_queries']} |
| **Pipeline Success Rate** | {summary['pipeline_success_rate']}% ({summary['successful_pipelines']}/{summary['total_queries']}) |
| **Execution Match Rate (Correctness)** | {summary['execution_match_rate']}% ({summary['sql_execution_matches']}/{summary['comparable_queries']} comparable) |
| **Average Response Time** | {summary['average_duration_ms']} ms |

## Metrics by Category
| Category | Total | Comparable | Success Rate | Match Rate | Avg Duration |
| --- | --- | --- | --- | --- | --- |
"""
    for cat, stats in summary["categories"].items():
        md += f"| **{cat}** | {stats['total_queries']} | {stats['comparable_queries']} | {stats['pipeline_success_rate']}% | {stats['execution_match_rate']}% | {stats['average_duration_ms']} ms |\n"

    md += "\n## Detailed Results\n"
    for i, item in enumerate(details):
        expected_sql_display = item['expected_sql'] if item['expected_sql'] else '-- None Provided --'
        md += f"""
### Query {i+1}: [{item['category'].upper()}] {item['question']}
- **Pipeline Success**: `{item['pipeline_success']}`
- **Execution Correctness (DB Match)**: `{'N/A (No reference SQL)' if not item['is_comparable'] else item['execution_match']}`
- **Duration**: `{item['duration_ms']} ms`
- **Expected SQL**:
  ```sql
  {expected_sql_display}
  ```
- **Generated SQL**:
  ```sql
  {item['generated_sql'] if item['generated_sql'] else '-- None Generated --'}
  ```
"""
        if item['error']:
            md += f"- **Error**: `{item['error']}`\n"
    return md

def print_console_report(summary: Dict[str, Any]):
    print("=" * 70)
    print("                     NLPSQL BENCHMARK REPORT")
    print("=" * 70)
    print(f"Timestamp:                    {summary['timestamp']}")
    print(f"Total Queries Evaluated:      {summary['total_queries']}")
    print(f"Pipeline Success Rate:        {summary['pipeline_success_rate']}% ({summary['successful_pipelines']}/{summary['total_queries']})")
    print(f"Execution Match Rate (Correctness): {summary['execution_match_rate']}% ({summary['sql_execution_matches']}/{summary['comparable_queries']} comparable)")
    print(f"Average Response Time:        {summary['average_duration_ms']:.2f} ms")
    print("-" * 70)
    print("CATEGORY BREAKDOWN:")
    print("-" * 70)
    for cat, stats in summary["categories"].items():
        print(f"Category: {cat:<15} | Success: {stats['pipeline_success_rate']:>6}% | Match: {stats['execution_match_rate']:>6}% | Avg Time: {stats['average_duration_ms']:>7.2f} ms")
    print("=" * 70)

if __name__ == "__main__":
    run_benchmark()
