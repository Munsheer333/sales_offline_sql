"""Adaptive offline cross-encoder reranking for uncertain retrieval results."""

import math
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from config import (
    RERANK_BATCH_SIZE,
    RERANK_CANDIDATES,
    RERANK_ENABLED,
    RERANK_LOW_CONFIDENCE_THRESHOLD,
    RERANK_MODEL_PATH,
    resolve_rerank_model_source,
)
from core.observability import log_event


_HEALTH = {
    "status": "disabled" if not RERANK_ENABLED else "configured",
    "model_path": RERANK_MODEL_PATH or None,
    "last_duration_ms": 0,
    "last_reason": "disabled" if not RERANK_ENABLED else "not_invoked",
    "error": None,
}
_WARMUP_LOCK = threading.Lock()
_WARMUP_STARTED = False


def adaptive_cross_encoder_rerank(
    question: str,
    results: Sequence[dict],
    result_type: str,
    scorer: Optional[Callable[[Sequence[Sequence[str]]], Sequence[float]]] = None,
    threshold: float = RERANK_LOW_CONFIDENCE_THRESHOLD,
    candidate_limit: int = RERANK_CANDIDATES,
) -> List[dict]:
    """Rerank uncertain candidates only; preserve normal retrieval latency."""
    ranked = [dict(item) for item in results]
    if len(ranked) < 2:
        return _annotate_skipped(ranked, "insufficient_candidates")
    top_confidence = float(ranked[0].get("retrieval_confidence", 0.0) or 0.0)
    if top_confidence >= threshold:
        return _annotate_skipped(ranked, "high_confidence")

    if scorer is None:
        if not is_cross_encoder_configured():
            return _annotate_skipped(ranked, "not_configured")
        start_cross_encoder_warmup()
        if not is_cross_encoder_ready():
            return _annotate_skipped(ranked, "warming")

    candidates = ranked[:max(2, candidate_limit)]
    pairs = [[question, _candidate_text(item, result_type)] for item in candidates]
    start = time.perf_counter()
    try:
        raw_scores = list((scorer or _predict)(pairs))
        if len(raw_scores) != len(candidates):
            raise ValueError("Cross-encoder returned an unexpected score count.")

        for item, raw_score in zip(candidates, raw_scores):
            cross_score = _normalize_score(float(raw_score))
            item["cross_encoder_score"] = round(cross_score, 6)
            item["reranker_applied"] = True
            item["reranker_reason"] = "low_confidence"
            item["rerank_score"] = round(
                (0.72 * cross_score) + (0.28 * float(item.get("rerank_score", 0.0) or 0.0)),
                6,
            )
            item["retrieval_confidence"] = round(
                max(float(item.get("retrieval_confidence", 0.0) or 0.0), item["rerank_score"]),
                4,
            )

        candidates.sort(
            key=lambda item: (
                item.get("rerank_score", 0.0),
                item.get("hybrid_score", 0.0),
                item.get("vector_score", 0.0),
            ),
            reverse=True,
        )
        duration_ms = int((time.perf_counter() - start) * 1000)
        _HEALTH.update({
            "status": "ready",
            "last_duration_ms": duration_ms,
            "last_reason": "low_confidence",
            "error": None,
        })
        log_event(
            "retrieval.cross_encoder_rerank",
            result="success",
            candidate_count=len(candidates),
            duration_ms=duration_ms,
        )
        return candidates + ranked[len(candidates):]
    except Exception as exc:
        _HEALTH.update({
            "status": "degraded",
            "last_duration_ms": int((time.perf_counter() - start) * 1000),
            "last_reason": "error",
            "error": str(exc),
        })
        log_event("retrieval.cross_encoder_rerank", result="error", error=str(exc))
        return _annotate_skipped(ranked, "error")


def is_cross_encoder_configured() -> bool:
    if not RERANK_ENABLED:
        return False
    try:
        return Path(resolve_rerank_model_source()).is_dir()
    except RuntimeError:
        return False


def get_cross_encoder_health() -> Dict[str, Any]:
    return dict(_HEALTH)


def is_cross_encoder_ready() -> bool:
    return _HEALTH.get("status") == "ready"


def start_cross_encoder_warmup() -> bool:
    """Load the local model in the background; never block a request."""
    global _WARMUP_STARTED
    if not is_cross_encoder_configured() or is_cross_encoder_ready():
        return False
    with _WARMUP_LOCK:
        if _WARMUP_STARTED:
            return False
        _WARMUP_STARTED = True
        _HEALTH.update({
            "status": "warming",
            "model_path": resolve_rerank_model_source(),
            "last_reason": "background_warmup",
            "error": None,
        })
        thread = threading.Thread(target=_warm_cross_encoder, name="nlpsql-reranker-warmup", daemon=True)
        thread.start()
        return True


@lru_cache(maxsize=1)
def _get_cross_encoder():
    if not is_cross_encoder_configured():
        raise RuntimeError("Offline reranker is not configured. Set RERANK_ENABLED=true and RERANK_MODEL_PATH.")
    from sentence_transformers import CrossEncoder

    return CrossEncoder(resolve_rerank_model_source(), local_files_only=True, device="cpu")


def _warm_cross_encoder() -> None:
    start = time.perf_counter()
    try:
        _get_cross_encoder()
        _HEALTH.update({
            "status": "ready",
            "last_duration_ms": int((time.perf_counter() - start) * 1000),
            "last_reason": "background_warmup_complete",
            "error": None,
        })
    except Exception as exc:
        _HEALTH.update({
            "status": "degraded",
            "last_duration_ms": int((time.perf_counter() - start) * 1000),
            "last_reason": "background_warmup_failed",
            "error": str(exc),
        })


def _predict(pairs: Sequence[Sequence[str]]) -> Sequence[float]:
    return _get_cross_encoder().predict(
        list(pairs),
        batch_size=RERANK_BATCH_SIZE,
        show_progress_bar=False,
    )


def _candidate_text(item: dict, result_type: str) -> str:
    if result_type == "schema":
        return " ".join(str(item.get(key) or "") for key in ["table", "column", "description"])
    return " ".join(str(item.get(key) or "") for key in ["question", "complexity", "sql"])


def _normalize_score(score: float) -> float:
    if 0.0 <= score <= 1.0:
        return score
    return 1.0 / (1.0 + math.exp(-score))


def _annotate_skipped(results: List[dict], reason: str) -> List[dict]:
    for item in results:
        item["reranker_applied"] = False
        item["reranker_reason"] = reason
    return results
