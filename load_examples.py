import json
import os
import psycopg2
from config import DB_URL
from core.phase2_embeddings import setup_pgvector, embed_and_store_examples

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    seed_path = os.path.join(base_dir, "seed_pairs.json")
    
    examples = []
    if os.path.exists(seed_path):
        with open(seed_path, "r") as f:
            examples = json.load(f)

    # Load gold_tests.json as well if it exists
    gold_path = os.path.join(base_dir, "data", "gold_tests.json")
    if os.path.exists(gold_path):
        with open(gold_path, "r") as f:
            gold_cases = json.load(f)
        for gc in gold_cases:
            # Map gold_sql to sql
            examples.append({
                "question": gc["question"],
                "sql": gc["gold_sql"],
                "complexity": gc.get("complexity", "GENERAL")
            })

    # Deduplicate examples by question (case-insensitive) to prevent multiple embeddings for the same question
    seen_questions = set()
    deduped_examples = []
    for ex in examples:
        q_lower = ex["question"].strip().lower()
        if q_lower not in seen_questions:
            deduped_examples.append(ex)
            seen_questions.add(q_lower)

    if not deduped_examples:
        print("No examples found to load.")
        return

    print(f"Loading {len(deduped_examples)} examples into vector store...")
    setup_pgvector(DB_URL)

    # Truncate table first for a clean setup
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("TRUNCATE TABLE nl_sql_pairs CASCADE;")
        conn.commit()
        cur.close()
        conn.close()
        print("Truncated nl_sql_pairs table.")
    except Exception as e:
        print(f"Warning: could not truncate table: {e}")

    count = embed_and_store_examples(deduped_examples, DB_URL)
    print(f"Successfully embedded {count} examples.")

if __name__ == "__main__":
    main()

