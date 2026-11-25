import os
from pathlib import Path
from dotenv import load_dotenv
from core.offline_policy import require_local_http_url, require_offline_embedding_mode

# Load environment variables
load_dotenv()

# Database Configuration
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

DB_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Local Model Configuration
OFFLINE_MODE = os.getenv("OFFLINE_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}
OLLAMA_BASE_URL = require_local_http_url(
    os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
    "OLLAMA_BASE_URL",
    offline_mode=OFFLINE_MODE,
)
OLLAMA_GENERATE_URL = f"{OLLAMA_BASE_URL}/api/generate"
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5-coder:7b")
FAST_LLM_MODEL = os.getenv("FAST_LLM_MODEL", "qwen2.5-coder:1.5b")
FAST_LLM_TIMEOUT_SECONDS = float(os.getenv("FAST_LLM_TIMEOUT_SECONDS", "6"))
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-base-en-v1.5")
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))
EMBED_LOCAL_FILES_ONLY = os.getenv("EMBED_LOCAL_FILES_ONLY", "true").strip().lower() in {"1", "true", "yes", "on"}
require_offline_embedding_mode(OFFLINE_MODE, EMBED_LOCAL_FILES_ONLY)
EMBED_MODEL_PATH = os.getenv("EMBED_MODEL_PATH", "").strip()
CURRENT_FINANCIAL_YEAR = os.getenv("CURRENT_FINANCIAL_YEAR", "2025-2026")
LLM_GENERATION_TIMEOUT_SECONDS = float(os.getenv("LLM_GENERATION_TIMEOUT_SECONDS", "20"))
LLM_REPAIR_TIMEOUT_SECONDS = float(os.getenv("LLM_REPAIR_TIMEOUT_SECONDS", "8"))
LLM_MAX_SQL_TOKENS = int(os.getenv("LLM_MAX_SQL_TOKENS", "512"))
LLM_MAX_REPAIR_TOKENS = int(os.getenv("LLM_MAX_REPAIR_TOKENS", "384"))
PIPELINE_TIMEOUT_SECONDS = float(os.getenv("PIPELINE_TIMEOUT_SECONDS", "30"))

# RAG Configuration
SEED_PAIRS_PATH = os.getenv("SEED_PAIRS_PATH", "seed_pairs.json")
TOP_K = int(os.getenv("TOP_K", "5"))
RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
RERANK_MODEL_PATH = os.getenv("RERANK_MODEL_PATH", "").strip()
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
RERANK_LOW_CONFIDENCE_THRESHOLD = float(os.getenv("RERANK_LOW_CONFIDENCE_THRESHOLD", "0.52"))
RERANK_CANDIDATES = int(os.getenv("RERANK_CANDIDATES", "10"))
RERANK_BATCH_SIZE = int(os.getenv("RERANK_BATCH_SIZE", "8"))

# Pipeline Optimization
USE_LLM_ROUTER = True  # Set to False to use faster rule-based classification
MAX_HEAL_RETRIES = 2    # Number of self-healing attempts

# Path Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = BASE_DIR


def resolve_embed_model_source() -> str:
    """Return a local model snapshot path when running in offline mode."""
    if not EMBED_LOCAL_FILES_ONLY:
        return EMBED_MODEL_PATH or EMBED_MODEL

    if EMBED_MODEL_PATH:
        configured = Path(EMBED_MODEL_PATH).expanduser()
        if configured.is_dir():
            return str(configured)
        raise RuntimeError(f"Offline embedding model directory does not exist: {configured}")

    cache_root = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{EMBED_MODEL.replace('/', '--')}"
    ref_path = cache_root / "refs" / "main"
    if ref_path.is_file():
        snapshot = cache_root / "snapshots" / ref_path.read_text(encoding="utf-8").strip()
        if snapshot.is_dir():
            return str(snapshot)

    raise RuntimeError(
        f"Offline embedding model '{EMBED_MODEL}' is not available locally. "
        "Set EMBED_MODEL_PATH to a downloaded model directory."
    )


def resolve_rerank_model_source() -> str:
    """Return an offline cross-encoder snapshot path without network access."""
    if RERANK_MODEL_PATH:
        configured = Path(RERANK_MODEL_PATH).expanduser()
        if configured.is_dir():
            return str(configured)
        raise RuntimeError(f"Offline reranker model directory does not exist: {configured}")

    cache_root = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{RERANK_MODEL.replace('/', '--')}"
    ref_path = cache_root / "refs" / "main"
    if ref_path.is_file():
        snapshot = cache_root / "snapshots" / ref_path.read_text(encoding="utf-8").strip()
        if snapshot.is_dir():
            return str(snapshot)

    raise RuntimeError(
        f"Offline reranker model '{RERANK_MODEL}' is not available locally. "
        "Set RERANK_MODEL_PATH to a downloaded cross-encoder directory."
    )
