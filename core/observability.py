"""
NLPSQL observability helpers.

The module is intentionally dependency-tolerant. Prometheus and OpenTelemetry
are used when installed; otherwise calls become cheap no-ops so local/offline
execution keeps working.
"""

import contextlib
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional
import threading


class PipelineProfiler:
    _local = threading.local()

    @classmethod
    def reset(cls):
        cls._local.metrics = {
            "router_ms": 0,
            "entity_extraction_ms": 0,
            "retrieval_ms": 0,
            "bm25_ms": 0,
            "pgvector_ms": 0,
            "reranker_ms": 0,
            "llm_ms": 0,
            "validator_ms": 0,
            "db_ms": 0,
            "confidence_route": "unknown",
            "planner_confidence": 0.0
        }

    @classmethod
    def get_metrics(cls) -> dict:
        if not hasattr(cls._local, "metrics"):
            cls.reset()
        return cls._local.metrics

    @classmethod
    def add_ms(cls, key: str, duration_seconds: float):
        metrics = cls.get_metrics()
        if key in metrics:
            metrics[key] += int(duration_seconds * 1000)

    @classmethod
    def add_ms_direct(cls, key: str, ms: int):
        metrics = cls.get_metrics()
        if key in metrics:
            metrics[key] += ms

    @classmethod
    def set_ms(cls, key: str, duration_seconds: float):
        metrics = cls.get_metrics()
        if key in metrics:
            metrics[key] = int(duration_seconds * 1000)

    @classmethod
    def set_value(cls, key: str, value):
        """Set an arbitrary metadata value (non-timing) in the profiler."""
        metrics = cls.get_metrics()
        metrics[key] = value



try:
    from prometheus_client import Counter, Gauge, Histogram, CONTENT_TYPE_LATEST, generate_latest, REGISTRY
    PROMETHEUS_AVAILABLE = True
except Exception:  # pragma: no cover - exercised when optional dependency is absent
    Counter = Gauge = Histogram = REGISTRY = None
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"
    PROMETHEUS_AVAILABLE = False


try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    OTEL_AVAILABLE = True
except Exception:  # pragma: no cover - exercised when optional dependency is absent
    trace = None
    Status = StatusCode = None
    OTEL_AVAILABLE = False


LOGGER_NAME = "nlpsql.observability"
SERVICE_NAME = "nlpsql"

_LOGGER_CONFIGURED = False
_METRICS = {}


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": int(time.time() * 1000),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "structured", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, default=str)


def get_logger() -> logging.Logger:
    global _LOGGER_CONFIGURED
    logger = logging.getLogger(LOGGER_NAME)
    if not _LOGGER_CONFIGURED:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
        level_name = os.getenv("NLPSQL_LOG_LEVEL", "WARNING").upper()
        logger.setLevel(getattr(logging, level_name, logging.WARNING))
        logger.propagate = False
        _LOGGER_CONFIGURED = True
    return logger


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    logger = get_logger()
    if not logger.isEnabledFor(level):
        return
    logger.log(level, event, extra={"structured": {"event": event, **fields}})


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def text_fingerprint(text: str) -> str:
    return hashlib.sha256((text or "").strip().lower().encode("utf-8")).hexdigest()[:16]


def get_tracer():
    if not OTEL_AVAILABLE:
        return None
    return trace.get_tracer(SERVICE_NAME)


@contextlib.contextmanager
def start_span(name: str, **attributes: Any):
    tracer = get_tracer()
    if not tracer:
        yield None
        return

    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


class StageTimer:
    def __init__(self, stage: str, labels: Optional[Dict[str, Any]] = None, log: bool = True):
        self.stage = stage
        self.labels = labels or {}
        self.log = log
        self.started_at = 0.0
        self.elapsed_seconds = 0.0
        self.elapsed_ms = 0
        self.outcome = "success"
        self.error = None
        self.span = None
        self._span_context = None

    def __enter__(self):
        self.started_at = time.perf_counter()
        self._span_context = start_span(f"nlpsql.{self.stage}", **_span_attributes(self.labels))
        self.span = self._span_context.__enter__()
        if self.log:
            log_event("stage.start", stage=self.stage, **self.labels)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.elapsed_seconds = time.perf_counter() - self.started_at
        self.elapsed_ms = int(self.elapsed_seconds * 1000)
        if exc is not None:
            self.outcome = "error"
            self.error = str(exc)

        observe_stage_duration(self.stage, self.elapsed_seconds, self.outcome)
        if self.span:
            self.span.set_attribute("nlpsql.stage", self.stage)
            self.span.set_attribute("nlpsql.duration_ms", self.elapsed_ms)
            self.span.set_attribute("nlpsql.outcome", self.outcome)
        if self.log:
            log_event(
                "stage.finish",
                stage=self.stage,
                outcome=self.outcome,
                duration_ms=self.elapsed_ms,
                error=self.error,
                **self.labels,
            )
        if self._span_context:
            self._span_context.__exit__(exc_type, exc, tb)
        return False


def observed_stage(stage: str, **labels: Any) -> StageTimer:
    return StageTimer(stage=stage, labels=_clean_labels(labels))


def active_request():
    return _ActiveRequest()


class DummyMetric:
    def labels(self, *args, **kwargs):
        return self
    def inc(self, *args, **kwargs):
        pass
    def dec(self, *args, **kwargs):
        pass
    def observe(self, *args, **kwargs):
        pass
    def set(self, *args, **kwargs):
        pass


class _ActiveRequest:
    def __enter__(self):
        try:
            metrics = _prometheus_metrics()
            gauge = metrics.get("active_requests")
            if gauge is not None:
                gauge.inc()
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            metrics = _prometheus_metrics()
            gauge = metrics.get("active_requests")
            if gauge is not None:
                gauge.dec()
        except Exception:
            pass
        return False


def observe_stage_duration(stage: str, seconds: float, outcome: str = "success") -> None:
    try:
        metrics = _prometheus_metrics()
        histogram = metrics.get("stage_duration")
        if histogram is not None:
            histogram.labels(stage=stage, outcome=outcome).observe(seconds)
    except Exception:
        pass


def observe_retrieval(kind: str, seconds: float, result_count: int, outcome: str = "success") -> None:
    try:
        metrics = _prometheus_metrics()
        histogram = metrics.get("retrieval_duration")
        if histogram is not None:
            histogram.labels(kind=kind, outcome=outcome).observe(seconds)
        counter = metrics.get("retrieval_results")
        if counter is not None:
            counter.labels(kind=kind).inc(max(result_count, 0))
    except Exception:
        pass


def observe_sql_generation(seconds: float, mode: str, outcome: str = "success") -> None:
    try:
        metrics = _prometheus_metrics()
        histogram = metrics.get("sql_generation_duration")
        if histogram is not None:
            histogram.labels(mode=mode or "unknown", outcome=outcome).observe(seconds)
    except Exception:
        pass


def observe_validation(seconds: float, outcome: str = "success") -> None:
    try:
        metrics = _prometheus_metrics()
        histogram = metrics.get("validation_duration")
        if histogram is not None:
            histogram.labels(outcome=outcome).observe(seconds)
    except Exception:
        pass


def observe_repair(seconds: float, repair_type: str, outcome: str = "success") -> None:
    try:
        metrics = _prometheus_metrics()
        histogram = metrics.get("repair_duration")
        if histogram is not None:
            histogram.labels(repair_type=repair_type or "unknown", outcome=outcome).observe(seconds)
        counter = metrics.get("repair_attempts")
        if counter is not None:
            counter.labels(repair_type=repair_type or "unknown", outcome=outcome).inc()
    except Exception:
        pass


def record_cache_event(cache: str, event: str) -> None:
    try:
        metrics = _prometheus_metrics()
        counter = metrics.get("cache_events")
        if counter is not None:
            counter.labels(cache=cache or "unknown", event=event or "unknown").inc()
    except Exception:
        pass


def record_pipeline_result(seconds: float, success: bool, generation_mode: str) -> None:
    try:
        metrics = _prometheus_metrics()
        outcome = "success" if success else "error"
        counter = metrics.get("pipeline_requests")
        if counter is not None:
            counter.labels(outcome=outcome, generation_mode=generation_mode or "unknown").inc()
        histogram = metrics.get("pipeline_duration")
        if histogram is not None:
            histogram.labels(outcome=outcome, generation_mode=generation_mode or "unknown").observe(seconds)
    except Exception:
        pass


def prometheus_metrics() -> bytes:
    if not PROMETHEUS_AVAILABLE:
        return b"# prometheus_client is not installed\n"
    _prometheus_metrics()
    return generate_latest()


def prometheus_content_type() -> str:
    return CONTENT_TYPE_LATEST


def observability_status() -> Dict[str, bool]:
    return {
        "prometheus_available": PROMETHEUS_AVAILABLE,
        "opentelemetry_available": OTEL_AVAILABLE,
    }


def _get_or_create_metric(cls, name: str, documentation: str, *args, **kwargs):
    if not PROMETHEUS_AVAILABLE or cls is None:
        return DummyMetric()
    base_name = name[:-6] if name.endswith("_total") else name
    if REGISTRY is not None and hasattr(REGISTRY, "_names_to_collectors"):
        if name in REGISTRY._names_to_collectors:
            return REGISTRY._names_to_collectors[name]
        if base_name in REGISTRY._names_to_collectors:
            return REGISTRY._names_to_collectors[base_name]
        for reg_name, col in list(REGISTRY._names_to_collectors.items()):
            if reg_name == name or reg_name == base_name or reg_name.startswith(base_name + "_"):
                return col
    try:
        return cls(name, documentation, *args, **kwargs)
    except Exception as exc:
        if REGISTRY is not None and hasattr(REGISTRY, "_names_to_collectors"):
            if name in REGISTRY._names_to_collectors:
                return REGISTRY._names_to_collectors[name]
            if base_name in REGISTRY._names_to_collectors:
                return REGISTRY._names_to_collectors[base_name]
            for reg_name, col in list(REGISTRY._names_to_collectors.items()):
                if reg_name == name or reg_name == base_name or reg_name.startswith(base_name + "_"):
                    return col
        return DummyMetric()


def _prometheus_metrics() -> Dict[str, Any]:
    if not PROMETHEUS_AVAILABLE:
        return {}
    if _METRICS:
        return _METRICS

    buckets = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)
    try:
        _METRICS.update({
        "pipeline_requests": _get_or_create_metric(
            Counter,
            "nlpsql_pipeline_requests_total",
            "Total NL2SQL pipeline requests.",
            ["outcome", "generation_mode"],
        ),
        "pipeline_duration": _get_or_create_metric(
            Histogram,
            "nlpsql_pipeline_duration_seconds",
            "End-to-end NL2SQL pipeline duration.",
            ["outcome", "generation_mode"],
            buckets=buckets,
        ),
        "stage_duration": _get_or_create_metric(
            Histogram,
            "nlpsql_stage_duration_seconds",
            "Pipeline stage duration.",
            ["stage", "outcome"],
            buckets=buckets,
        ),
        "retrieval_duration": _get_or_create_metric(
            Histogram,
            "nlpsql_retrieval_duration_seconds",
            "Retrieval latency by retrieval kind.",
            ["kind", "outcome"],
            buckets=buckets,
        ),
        "retrieval_results": _get_or_create_metric(
            Counter,
            "nlpsql_retrieval_results_total",
            "Total retrieval results returned.",
            ["kind"],
        ),
        "sql_generation_duration": _get_or_create_metric(
            Histogram,
            "nlpsql_sql_generation_duration_seconds",
            "SQL generation latency.",
            ["mode", "outcome"],
            buckets=buckets,
        ),
        "validation_duration": _get_or_create_metric(
            Histogram,
            "nlpsql_validation_duration_seconds",
            "Validation and execution latency.",
            ["outcome"],
            buckets=buckets,
        ),
        "repair_duration": _get_or_create_metric(
            Histogram,
            "nlpsql_repair_duration_seconds",
            "SQL repair latency.",
            ["repair_type", "outcome"],
            buckets=buckets,
        ),
        "repair_attempts": _get_or_create_metric(
            Counter,
            "nlpsql_repair_attempts_total",
            "SQL repair attempts.",
            ["repair_type", "outcome"],
        ),
        "cache_events": _get_or_create_metric(
            Counter,
            "nlpsql_cache_events_total",
            "Cache hit/miss/store/error events.",
            ["cache", "event"],
        ),
        "active_requests": _get_or_create_metric(
            Gauge,
            "nlpsql_active_requests",
            "In-flight NL2SQL requests.",
        ),
    })
    except Exception:
        pass
    return _METRICS


def _span_attributes(labels: Dict[str, Any]) -> Dict[str, Any]:
    return {f"nlpsql.{key}": value for key, value in _clean_labels(labels).items()}


def _clean_labels(labels: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in labels.items() if value is not None}
