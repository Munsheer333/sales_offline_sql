import sys
import logging
import time

# Set up verbose logging to see where it blocks
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

print("Starting pipeline test...")
start_time = time.time()

print("1. Loading config...")
import config
print(f"Ollama URL: {config.OLLAMA_GENERATE_URL}")
print(f"Embed Model: {config.EMBED_MODEL}")
print(f"Offline Mode: {config.OFFLINE_MODE}")

print("\n2. Trying to import and load embedding model...")
try:
    from core.phase2_embeddings import get_local_embedder
    print("Calling get_local_embedder()...")
    embedder = get_local_embedder()
    print("Embedder loaded successfully!")
except Exception as e:
    print(f"Failed to load embedder: {e}", file=sys.stderr)

print("\n3. Testing connection to database...")
try:
    import psycopg2
    conn = psycopg2.connect(config.DB_URL)
    print("Database connected successfully!")
    conn.close()
except Exception as e:
    print(f"Database connection failed: {e}", file=sys.stderr)

print("\n4. Testing Ollama connection/status...")
try:
    import requests
    print(f"Checking Ollama status at {config.OLLAMA_BASE_URL}/api/tags...")
    r = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
    print(f"Ollama Response Code: {r.status_code}")
    print(f"Ollama Response JSON: {r.json()}")
except Exception as e:
    print(f"Failed to connect to Ollama: {e}", file=sys.stderr)

print("\n5. Testing run_pipeline with a simple question...")
try:
    from core.phase6_orchestrator import run_pipeline
    question = "List the names of all unique products sold in the state of GUJARAT."
    print(f"Running pipeline for question: '{question}'...")
    res = run_pipeline(
        question=question,
        conn_str=config.DB_URL,
        model=config.LLM_MODEL,
        schema_path="data/schema_metadata.json"
    )
    print("\nPipeline Result:")
    print(res)
except Exception as e:
    print(f"Pipeline failed: {e}", file=sys.stderr)

print(f"\nDiagnostic finished in {time.time() - start_time:.2f} seconds.")
