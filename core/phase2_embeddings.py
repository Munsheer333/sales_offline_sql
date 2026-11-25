"""
NLPSQL SYSTEM — PHASE 02
Offline Embedding & Vector Store (pgvector via sentence-transformers)
Uses a lazy-loaded local embedding model so application imports stay cheap.
"""

import json
import asyncio
import os
import psycopg2
from functools import lru_cache
from typing import Any, List
from config import EMBED_MODEL, EMBED_DIM, EMBED_LOCAL_FILES_ONLY, resolve_embed_model_source
from core.phase2_hybrid_retriever import (
    create_full_text_indexes,
    retrieve_hybrid_schema,
    retrieve_hybrid_examples,
    vector_schema_results,
    vector_example_results,
)
from core.phase6_query_pattern_engine import create_query_pattern_cache_tables

_EMBEDDING_HEALTH = {"status": "unknown", "model": EMBED_MODEL, "dimension": EMBED_DIM, "error": None}

@lru_cache(maxsize=1)
def get_local_embedder():
    """Load the configured sentence-transformers model once per process."""
    if EMBED_LOCAL_FILES_ONLY:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(resolve_embed_model_source(), device="cpu")


@lru_cache(maxsize=128)
def _get_cached_embedding(text: str) -> List[float]:
    model = get_local_embedder()
    embedding = model.encode(
        text,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)


def get_local_embedding(text: str, embedder=None) -> List[float]:
    """Generate one normalized local embedding with dimension validation."""
    try:
        if embedder is None:
            vector = _get_cached_embedding(text)
        else:
            embedding = embedder.encode(
                text,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            vector = embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)

        if len(vector) != EMBED_DIM:
            raise ValueError(
                f"Embedding model '{EMBED_MODEL}' returned {len(vector)} dimensions; expected {EMBED_DIM}"
            )
        _EMBEDDING_HEALTH.update({"status": "healthy", "error": None})
        return vector
    except Exception as e:
        _EMBEDDING_HEALTH.update({"status": "degraded_zero_vector", "error": str(e)})
        print(f"  [!] Embedding failed for text fragment: {e}")
        # Preserve the existing degraded-mode contract for retrieval callers.
        return [0.0] * EMBED_DIM


def get_embedding_health() -> dict:
    """Expose whether retrieval is using real local embeddings or lexical degraded mode."""
    return dict(_EMBEDDING_HEALTH)


def get_ollama_embedding(text: str) -> List[float]:
    """Migration-safe alias for callers using the previous adapter name."""
    return get_local_embedding(text)


def setup_pgvector(conn_str: str):
    """Initializes pgvector tables."""
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    
    # Table for schema embeddings
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS nlpsql_schema_embeddings (
            id SERIAL PRIMARY KEY,
            table_name TEXT NOT NULL,
            column_name TEXT,
            description TEXT NOT NULL,
            embedding vector({EMBED_DIM}),
            embedding_model TEXT NOT NULL DEFAULT 'legacy',
            metadata JSONB,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # Table for training examples (if not exists)
    # We use nl_sql_pairs as the primary source, but this can be a mirror or cache
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS nl_sql_pairs (
            id SERIAL PRIMARY KEY,
            question TEXT NOT NULL,
            sql_query TEXT NOT NULL,
            intent VARCHAR,
            question_embedding vector({EMBED_DIM}),
            embedding_model TEXT NOT NULL DEFAULT 'legacy',
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        ALTER TABLE nlpsql_schema_embeddings
        ADD COLUMN IF NOT EXISTS embedding_model TEXT NOT NULL DEFAULT 'legacy';
    """)
    cur.execute("""
        ALTER TABLE nl_sql_pairs
        ADD COLUMN IF NOT EXISTS embedding_model TEXT NOT NULL DEFAULT 'legacy';
    """)

    # Table for query history
    cur.execute("""
        CREATE TABLE IF NOT EXISTS nlpsql_query_history (
            id SERIAL PRIMARY KEY,
            question TEXT NOT NULL,
            generated_sql TEXT NOT NULL,
            complexity VARCHAR(50),
            was_valid BOOLEAN,
            execution_time_ms INTEGER,
            row_count INTEGER,
            error_message TEXT,
            feedback INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        ALTER TABLE nlpsql_query_history
        ADD COLUMN IF NOT EXISTS latency_info JSONB;
    """)
    
    cur.execute("CREATE INDEX IF NOT EXISTS idx_schema_embed ON nlpsql_schema_embeddings USING hnsw (embedding vector_cosine_ops);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pairs_embed ON nl_sql_pairs USING hnsw (question_embedding vector_cosine_ops);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_schema_embedding_model ON nlpsql_schema_embeddings (embedding_model);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pairs_embedding_model ON nl_sql_pairs (embedding_model);")
    create_full_text_indexes(cur)
    create_query_pattern_cache_tables(cur)
    
    conn.commit()
    cur.close()
    conn.close()

def embed_and_store_schema(schema_metadata: dict, conn_str: str, embedder=None):
    """Embeds schema fragments and stores in DB."""
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    cur.execute("DELETE FROM nlpsql_schema_embeddings WHERE embedding_model = %s", (EMBED_MODEL,))
    
    count = 0
    for table in schema_metadata["tables"]:
        tname = table['table_name']
        print(f"--- Processing Table: {tname} ---")
        
        # Table level
        table_desc = f"Table {tname}: {table.get('description', '')}. Contains {table['total_rows']:,} records."
        emb = get_local_embedding(table_desc, embedder)
        _require_nonzero_embedding(emb)
        cur.execute(
            "INSERT INTO nlpsql_schema_embeddings (table_name, column_name, description, embedding, embedding_model, metadata) VALUES (%s, %s, %s, %s, %s, %s)",
            (tname, None, table_desc, emb, EMBED_MODEL, json.dumps({"type": "table"}))
        )
        count += 1
        
        # Column level
        print(f"  Embedding {len(table['columns'])} columns...")
        for col in table["columns"]:
            col_desc = f"Column {col['clean_name']} in table {tname}: {col.get('description', '')}. Type: {col['pg_type']}."
            emb = get_local_embedding(col_desc, embedder)
            _require_nonzero_embedding(emb)
            cur.execute(
                "INSERT INTO nlpsql_schema_embeddings (table_name, column_name, description, embedding, embedding_model, metadata) VALUES (%s, %s, %s, %s, %s, %s)",
                (tname, col["clean_name"], col_desc, emb, EMBED_MODEL, json.dumps({"type": "column"}))
            )
            count += 1

    cur.execute("DELETE FROM nlpsql_schema_embeddings WHERE embedding_model <> %s", (EMBED_MODEL,))
    conn.commit()
    cur.close()
    conn.close()
    print(f"DONE: Successfully embedded {count} schema fragments.")
    return count

def retrieve_similar_schema(question: str, conn_str: str, embedder=None, top_k: int = 8) -> List[dict]:
    emb = get_local_embedding(question, embedder)
    try:
        return retrieve_hybrid_schema(question, conn_str, emb, top_k=top_k)
    except Exception as e:
        print(f"  [!] Hybrid schema retrieval failed, falling back to vector-only: {e}")
        return vector_schema_results(question, conn_str, emb, top_k=top_k, retrieval_method="vector_fallback")

def retrieve_similar_examples(question: str, conn_str: str, embedder=None, top_k: int = 5) -> List[dict]:
    emb = get_local_embedding(question, embedder)
    try:
        return retrieve_hybrid_examples(question, conn_str, emb, top_k=top_k)
    except Exception as e:
        print(f"  [!] Hybrid example retrieval failed, falling back to vector-only: {e}")
        return vector_example_results(question, conn_str, emb, top_k=top_k, retrieval_method="vector_fallback")


async def retrieve_similar_schema_async(question: str, conn_str: str, embedder=None, top_k: int = 8) -> List[dict]:
    return await asyncio.to_thread(retrieve_similar_schema, question, conn_str, embedder, top_k)


async def retrieve_similar_examples_async(question: str, conn_str: str, embedder=None, top_k: int = 5) -> List[dict]:
    return await asyncio.to_thread(retrieve_similar_examples, question, conn_str, embedder, top_k)

def embed_texts(texts: List[str], embedder=None) -> List[List[float]]:
    """Helper to embed a list of strings."""
    return [get_local_embedding(text, embedder) for text in texts]


def _require_nonzero_embedding(embedding: List[float]) -> None:
    if not any(value != 0.0 for value in embedding):
        raise RuntimeError(
            f"Embedding model '{EMBED_MODEL}' returned a zero vector; refusing to persist invalid embeddings"
        )


def embed_and_store_examples(examples: List[dict], conn_str: str, embedder=None):
    """
    Embeds natural language questions from few-shot examples and stores them in DB.
    Expected format for each example: {"question": "...", "sql": "...", "complexity": "..."}
    """
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    cur.execute("DELETE FROM nl_sql_pairs WHERE embedding_model = %s", (EMBED_MODEL,))
    
    count = 0
    for ex in examples:
        emb = get_local_embedding(ex["question"], embedder)
        _require_nonzero_embedding(emb)
        cur.execute(
            "INSERT INTO nl_sql_pairs (question, sql_query, intent, question_embedding, embedding_model) VALUES (%s, %s, %s, %s, %s)",
            (ex["question"], ex["sql"], ex.get("complexity", "GENERAL"), emb, EMBED_MODEL)
        )
        count += 1

    cur.execute("DELETE FROM nl_sql_pairs WHERE embedding_model <> %s", (EMBED_MODEL,))
    conn.commit()
    cur.close()
    conn.close()
    return count
