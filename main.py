import sys
import io
import json
import asyncio
import logging
import queue
import threading
from typing import List, Optional, Dict, Any, Generator
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pathlib import Path

# Import our core modules
from core.phase6_orchestrator import run_pipeline, get_query_history, save_feedback
from core.phase1_ingestion import analyze_csv, save_schema_metadata
from core.phase2_embeddings import (
    setup_pgvector, embed_and_store_schema
)
from core.phase7_evaluation import get_accuracy_report
from core.observability import (
    active_request,
    observability_status,
    prometheus_content_type,
    prometheus_metrics,
)
from config import DB_URL, LLM_MODEL, OFFLINE_MODE

# Configure FastAPI
app = FastAPI(title="NLPSQL API")

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def observability_middleware(request, call_next):
    with active_request():
        return await call_next(request)

# --- Models ---
class QueryRequest(BaseModel):
    question: str
    model: Optional[str] = LLM_MODEL
    conn_str: Optional[str] = DB_URL

class FeedbackRequest(BaseModel):
    query_id: int
    feedback: int

# --- Real-time Log Capture ---
class LogQueue(io.TextIOBase):
    def __init__(self):
        self.queue = queue.Queue()
    def write(self, s):
        if s.strip():
            self.queue.put(s.strip())
        return len(s)
    def get_logs(self):
        while not self.queue.empty():
            yield self.queue.get()

# --- Endpoints ---

@app.get("/api/status")
def get_status():
    schema_path = Path("data/schema_metadata.json")
    schema_loaded = schema_path.exists()
    tables = []
    if schema_loaded:
        try:
            with open(schema_path) as f:
                meta = json.load(f)
                tables = [t["table_name"] for t in meta.get("tables", [])]
        except: pass
    
    return {
        "status": "online",
        "schema_loaded": schema_loaded,
        "tables": tables,
        "model": LLM_MODEL,
        "offline_mode": OFFLINE_MODE,
        "observability": observability_status(),
    }


@app.get("/metrics")
def get_metrics():
    return Response(content=prometheus_metrics(), media_type=prometheus_content_type())

@app.post("/api/query")
def execute_query(req: QueryRequest):
    # Use standard def so FastAPI runs it in a threadpool, preventing server blocking
    try:
        # Redirect stdout temporarily to capture logs for the response (non-streaming fallback)
        stdout_orig = sys.stdout
        sys.stdout = io.StringIO()
        
        result = run_pipeline(
            question=req.question,
            conn_str=req.conn_str,
            model=req.model
        )
        
        logs = sys.stdout.getvalue().splitlines()
        sys.stdout = stdout_orig
        
        return {
            "result": result,
            "logs": logs
        }
    except Exception as e:
        sys.stdout = stdout_orig
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/query/stream")
async def stream_query(question: str):
    """Streaming endpoint for real-time log visualization."""
    log_q = LogQueue()
    
    def run_and_capture():
        stdout_orig = sys.stdout
        sys.stdout = log_q
        try:
            print(f"--- Starting Pipeline for: {question} ---")
            result = run_pipeline(question=question, conn_str=DB_URL, model=LLM_MODEL)
            
            # Use a custom encoder to handle non-serializable types like datetime.date and Decimal
            def json_serial(obj):
                from datetime import date, datetime
                from decimal import Decimal
                if isinstance(obj, (date, datetime)):
                    return obj.isoformat()
                if isinstance(obj, Decimal):
                    return float(obj)
                return str(obj)

            # Send the final result as a special JSON log line
            print(f"RESULT_JSON:{json.dumps(result, default=json_serial)}")
        except Exception as e:
            print(f"[ERROR] {str(e)}")
        finally:
            sys.stdout = stdout_orig
            log_q.queue.put("DONE")

    threading.Thread(target=run_and_capture).start()

    async def event_generator():
        while True:
            await asyncio.sleep(0.1)
            try:
                line = log_q.queue.get_nowait()
                if line == "DONE":
                    yield "event: DONE\ndata: ok\n\n"
                    break
                yield f"data: {line}\n\n"
            except queue.Empty:
                continue

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/history")
def get_history(limit: int = 50):
    return get_query_history(DB_URL, limit=limit)

@app.post("/api/feedback")
def post_feedback(req: FeedbackRequest):
    save_feedback(DB_URL, req.query_id, req.feedback)
    return {"status": "success"}

@app.get("/api/evaluation")
def get_evaluation():
    return get_accuracy_report(DB_URL)

@app.get("/api/performance")
def get_performance(limit: int = 100):
    try:
        from core.db_pool import pooled_connect
        conn = pooled_connect(DB_URL)
        cur = conn.cursor()
        cur.execute(
            """SELECT latency_info, execution_time_ms, was_valid, question, created_at 
               FROM nlpsql_query_history 
               WHERE latency_info IS NOT NULL 
               ORDER BY id DESC LIMIT %s""",
            (limit,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    stats = {
        "router_ms": [],
        "entity_extraction_ms": [],
        "retrieval_ms": [],
        "bm25_ms": [],
        "pgvector_ms": [],
        "reranker_ms": [],
        "llm_ms": [],
        "validator_ms": [],
        "db_ms": []
    }
    
    total_queries = len(rows)
    valid_count = 0
    llm_queries_count = 0
    
    for row in rows:
        latency_info = row[0]
        was_valid = row[2]
        if was_valid:
            valid_count += 1
            
        if latency_info:
            if isinstance(latency_info, str):
                try:
                    latency_info = json.loads(latency_info)
                except:
                    continue
            for key in stats:
                if key in latency_info:
                    stats[key].append(latency_info[key])
            if latency_info.get("llm_ms", 0) > 0:
                llm_queries_count += 1
                    
    averages = {}
    for key, values in stats.items():
        averages[key] = round(sum(values) / len(values), 2) if values else 0.0

    return {
        "total_queries": total_queries,
        "valid_rate": round(valid_count / total_queries * 100, 2) if total_queries else 0.0,
        "llm_usage_percentage": round(llm_queries_count / total_queries * 100, 2) if total_queries else 0.0,
        "averages": averages,
        "raw_runs": [
            {
                "created_at": row[4].isoformat() if row[4] else None,
                "question": row[3],
                "was_valid": row[2],
                "total_duration_ms": row[1],
                "latency_info": row[0] if isinstance(row[0], dict) else json.loads(row[0] or "{}")
            }
            for row in rows
        ]
    }

@app.get("/api/routing-stats")
def get_routing_stats(limit: int = 200):
    """Measure percentage of queries going through each confidence route."""
    try:
        from core.db_pool import pooled_connect
        conn = pooled_connect(DB_URL)
        cur = conn.cursor()
        cur.execute(
            """SELECT latency_info FROM nlpsql_query_history 
               WHERE latency_info IS NOT NULL 
               ORDER BY id DESC LIMIT %s""",
            (limit,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    total = len(rows)
    route_counts = {"high_deterministic": 0, "medium_memory": 0, "low_llm": 0, "unknown": 0}
    llm_used_count = 0

    for row in rows:
        latency_info = row[0]
        if latency_info:
            if isinstance(latency_info, str):
                try:
                    latency_info = json.loads(latency_info)
                except:
                    continue
            route = latency_info.get("confidence_route", "unknown")
            route_counts[route] = route_counts.get(route, 0) + 1
            if latency_info.get("llm_ms", 0) > 0:
                llm_used_count += 1

    route_percentages = {
        route: round(count / total * 100, 2) if total else 0.0
        for route, count in route_counts.items()
    }

    return {
        "total_queries": total,
        "route_counts": route_counts,
        "route_percentages": route_percentages,
        "llm_used_count": llm_used_count,
        "llm_usage_percentage": round(llm_used_count / total * 100, 2) if total else 0.0,
        "target_llm_percentage": 10.0,
        "target_met": (llm_used_count / total * 100 if total else 0.0) < 10.0
    }

@app.get("/api/intent-cache-stats")
def get_intent_cache_stats():
    """Return intent cache hit/miss statistics."""
    try:
        from core.intent_cache import get_intent_cache
        cache = get_intent_cache()
        return cache.stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/setup/initialize")
def initialize_system(conn_str: Optional[str] = DB_URL):
    try:
        setup_pgvector(conn_str)
        schema_path = Path("data/schema_metadata.json")
        if not schema_path.exists():
            return {"success": False, "error": "No schema metadata found."}
        
        with open(schema_path) as f:
            meta = json.load(f)
        
        count = embed_and_store_schema(meta, conn_str)
        return {"success": True, "embedded_count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
