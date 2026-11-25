"""
NLPSQL SYSTEM — Main Streamlit Application
Production-grade offline NL→SQL generator
"""

import streamlit as st
import pandas as pd
import json
import os
import time
from pathlib import Path
from config import DB_URL, LLM_MODEL, DB_NAME, OLLAMA_BASE_URL
from urllib.parse import urlparse, urlunparse
import base64
import streamlit.components.v1 as components
import sys

# Automatically evict cached core modules so long-running Streamlit processes always execute latest code
for _mod in list(sys.modules.keys()):
    if _mod == "core" or _mod.startswith("core."):
        sys.modules.pop(_mod, None)

def set_db_name_in_url(url: str, db_name: str) -> str:
    try:
        parsed = urlparse(url)
        return urlunparse(parsed._replace(path=f"/{db_name}"))
    except Exception:
        if '/' in url:
            parts = url.rsplit('/', 1)
            if '?' in parts[1]:
                subparts = parts[1].split('?', 1)
                return f"{parts[0]}/{db_name}?{subparts[1]}"
            return f"{parts[0]}/{db_name}"
        return url


# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NLPSQL System",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  html, body, [class*="css"] { font-family: Arial, sans-serif; }

  .main { background: #0a0e1a; }
  .block-container { padding: 2rem 2.5rem; max-width: 1400px; }

  /* Sidebar */
  [data-testid="stSidebar"] {
    background: #0d1117;
    border-right: 1px solid #1e2d3d;
  }
  [data-testid="stSidebar"] .block-container { padding: 1.5rem 1rem; }

  /* Logo */
  .nlpsql-logo {
    background: linear-gradient(135deg, #00d4ff, #7c3aed);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-size: 1.6rem;
    font-weight: 700;
    letter-spacing: -0.5px;
    font-family: Menlo, Consolas, monospace;
    margin-bottom: 0.2rem;
  }
  .nlpsql-sub {
    color: #4a5568;
    font-size: 0.72rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    margin-bottom: 1.5rem;
  }

  /* Query input & other text inputs */
  .stTextArea textarea, .stTextInput input {
    background: #ffffff !important;
    border: 1px solid #d1d5db !important;
    border-radius: 10px !important;
    color: #111827 !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 0.95rem !important;
    padding: 1rem !important;
    transition: all 0.2s;
  }
  .stTextArea textarea:focus, .stTextInput input:focus {
    border-color: #00d4ff !important;
    box-shadow: 0 0 0 2px rgba(0, 212, 255, 0.1) !important;
  }



  /* Complexity badges */
  .badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .badge-SIMPLE   { background: #052e16; color: #22c55e; }
  .badge-MEDIUM   { background: #0c1a3a; color: #60a5fa; }
  .badge-HARD     { background: #2d1b00; color: #f59e0b; }
  .badge-ADVANCED { background: #2d1200; color: #fb923c; }
  .badge-ULTRA    { background: #2d0000; color: #f87171; }

  /* Metric cards */
  .metric-card {
    background: #0d1117;
    border: 1px solid #1e2d3d;
    border-radius: 10px;
    padding: 1rem 1.25rem;
    text-align: center;
  }
  .metric-val { font-size: 1.8rem; font-weight: 700; color: #00d4ff; }
  .metric-lbl { font-size: 0.72rem; color: #4a5568; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 2px; }

  /* Healed badge */
  .healed-badge {
    background: #1a2d1a;
    border: 1px solid #22c55e;
    color: #22c55e;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.72rem;
  }

  /* Buttons */
  .stButton > button {
    background: #000000 !important;
    border: 1px solid #1e2d3d !important;
    color: #e2e8f0 !important;
    border-radius: 8px !important;
    font-weight: 500 !important;
    transition: all 0.2s !important;
  }
  .stButton > button:hover {
    border-color: #4a5568 !important;
    background: #111111 !important;
  }

  /* Run button */
  .run-btn > button {
    background: #000000 !important;
    color: #ffffff !important;
    font-weight: 600 !important;
    border: 1px solid #00d4ff !important;
    width: 100% !important;
    padding: 0.6rem !important;
  }
  .run-btn > button:hover {
    background: #0a0a0a !important;
    border-color: #7c3aed !important;
  }

  /* Dataframe */
  .stDataFrame { border-radius: 8px; overflow: hidden; }

  /* Section headers */
  .section-header {
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #4a5568;
    margin: 1.5rem 0 0.75rem;
    padding-bottom: 0.4rem;
    border-bottom: 1px solid #1e2d3d;
  }

  /* Status dot */
  .dot-online { color: #22c55e; font-size: 0.7rem; }
  .dot-offline { color: #f87171; font-size: 0.7rem; }

  /* Info panels */
  .info-panel {
    background: #0d1117;
    border: 1px solid #1e2d3d;
    border-radius: 8px;
    padding: 0.75rem 1rem;
    font-size: 0.83rem;
    color: #718096;
    margin: 0.5rem 0;
  }
  .info-panel code {
    font-family: 'JetBrains Mono', monospace;
    color: #00d4ff;
    font-size: 0.8rem;
  }

  /* Tabs */
  .stTabs [data-baseweb="tab-list"] {
    background: #0d1117;
    border-radius: 8px;
    padding: 4px;
    gap: 4px;
    border: 1px solid #1e2d3d;
  }
  .stTabs [data-baseweb="tab"] {
    background: transparent !important;
    border-radius: 6px !important;
    color: #4a5568 !important;
    font-size: 0.82rem !important;
  }
  .stTabs [aria-selected="true"] {
    background: #1e2d3d !important;
    color: #e2e8f0 !important;
  }

  /* Warning box */
  .warn-box {
    background: #2d1b00;
    border: 1px solid #f59e0b44;
    border-left: 3px solid #f59e0b;
    border-radius: 8px;
    padding: 0.75rem 1rem;
    font-size: 0.82rem;
    color: #fbbf24;
    margin: 0.5rem 0;
  }
  .error-box {
    background: #2d0000;
    border: 1px solid #f8717144;
    border-left: 3px solid #f87171;
    border-radius: 8px;
    padding: 0.75rem 1rem;
    font-size: 0.82rem;
    color: #fca5a5;
    margin: 0.5rem 0;
  }
  .success-box {
    background: #052e16;
    border: 1px solid #22c55e44;
    border-left: 3px solid #22c55e;
    border-radius: 8px;
    padding: 0.75rem 1rem;
    font-size: 0.82rem;
    color: #86efac;
    margin: 0.5rem 0;
  }
</style>
""", unsafe_allow_html=True)


# ── Session state defaults ────────────────────────────────────────────────────
if "session_id" not in st.session_state:
    import uuid
    st.session_state.session_id = str(uuid.uuid4())
if "schema_loaded" not in st.session_state:
    st.session_state.schema_loaded = False
if "embedder" not in st.session_state:
    st.session_state.embedder = None
if "query_result" not in st.session_state:
    st.session_state.query_result = None
if "base_conn_str" not in st.session_state:
    st.session_state.base_conn_str = DB_URL
if "conn_str" not in st.session_state:
    st.session_state.conn_str = DB_URL
if "model" not in st.session_state:
    st.session_state.model = LLM_MODEL


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="nlpsql-logo">NLPSQL</div>', unsafe_allow_html=True)
    st.markdown('<div class="nlpsql-sub">Offline NL → SQL Engine</div>', unsafe_allow_html=True)

    st.markdown('<div class="section-header">Database Mode</div>', unsafe_allow_html=True)
    db_mode = st.radio(
        "Select Database",
        ["Main E-Commerce Database", "Custom Uploads (upload_db)"],
        index=0,
        label_visibility="collapsed",
        key="db_mode"
    )

    st.markdown('<div class="section-header">Database Connection Settings</div>', unsafe_allow_html=True)
    input_url = st.text_input(
        "Connection URL",
        value=st.session_state.base_conn_str,
        type="password",
        label_visibility="collapsed"
    )
    st.session_state.base_conn_str = input_url
    
    # Dynamically compute conn_str based on mode
    target_db = "upload_db" if db_mode == "Custom Uploads (upload_db)" else DB_NAME
    st.session_state.conn_str = set_db_name_in_url(input_url, target_db)
    
    schema_filename = "schema_metadata_upload.json" if target_db == "upload_db" else "schema_metadata.json"
    st.session_state.schema_path = f"data/{schema_filename}"

    # Test connection
    if st.button("Test Connection", use_container_width=True):
        try:
            import psycopg2
            psycopg2.connect(st.session_state.conn_str).close()
            st.markdown('<div class="success-box">✓ Connected successfully</div>', unsafe_allow_html=True)
        except Exception as e:
            st.markdown(f'<div class="error-box">✗ {str(e)[:80]}</div>', unsafe_allow_html=True)

    st.markdown('<div class="section-header">LLM Model</div>', unsafe_allow_html=True)
    
    @st.cache_data(ttl=60)
    def get_installed_ollama_models(base_url: str, default_model: str) -> list:
        try:
            import requests
            resp = requests.get(f"{base_url}/api/tags", timeout=1.5)
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                names = []
                for m in models:
                    name = m.get("name")
                    if name:
                        name_lower = name.lower()
                        if "embed" in name_lower or "bge-" in name_lower or "colbert" in name_lower:
                            continue
                        names.append(name)
                if default_model in names:
                    names.remove(default_model)
                names.insert(0, default_model)
                
                for common in ["qwen2.5-coder:7b", "deepseek-coder:6.7b", "llama3:latest", "llama3:8b"]:
                    if common in names and common != default_model:
                        names.remove(common)
                        names.append(common)
                return names
        except Exception:
            pass
        fallbacks = ["qwen2.5-coder:1.5b", "qwen2.5-coder:7b", "deepseek-coder:6.7b", "llama3:latest"]
        if default_model not in fallbacks:
            fallbacks.insert(0, default_model)
        return fallbacks

    installed_models = get_installed_ollama_models(OLLAMA_BASE_URL, LLM_MODEL)
    default_index = 0
    if st.session_state.model in installed_models:
        default_index = installed_models.index(st.session_state.model)
        
    model_choice = st.selectbox(
        "Ollama model",
        installed_models,
        index=default_index,
        label_visibility="collapsed"
    )
    st.session_state.model = model_choice

    st.markdown('<div class="section-header">Schema Status</div>', unsafe_allow_html=True)
    schema_path = Path(st.session_state.schema_path)
    if schema_path.exists():
        with open(schema_path) as f:
            meta = json.load(f)
        tables = meta.get("tables", [])
        st.markdown(f'<div class="success-box">✓ Schema loaded · {len(tables)} tables</div>', unsafe_allow_html=True)
        for t in tables:
            st.markdown(f'<div class="info-panel">📋 <code>{t["table_name"]}</code> · {t["total_rows"]:,} rows</div>', unsafe_allow_html=True)
        st.session_state.schema_loaded = True
    else:
        st.markdown('<div class="warn-box">⚠ No schema loaded yet.<br>Go to Setup tab.</div>', unsafe_allow_html=True)
        st.session_state.schema_loaded = False

    st.markdown('<div class="section-header">Quick Examples</div>', unsafe_allow_html=True)
    example_questions = [
        "What is the actual sales in TMT for Retail HSD from Dec 1 2025 to Dec 15 2025?",
        "Show actual sales for product MS in MT for Retail in Oct 2025.",
        "Rank companies by industry performance sales in AP state.",
        "Show industry performance for HPCL in district AGRA for fiscal year 2021-2022.",
        "Get the total target quantity in KL from M60_LEVEL for strategic business unit I&C in North zone."
    ]
    for q in example_questions:
        if st.button(q, use_container_width=True, key=f"ex_{q[:20]}"):
            st.session_state["execute_question"] = q
            st.rerun()


# ── Main content ──────────────────────────────────────────────────────────────
tab_query, tab_schema, tab_setup, tab_history, tab_eval, tab_perf, tab_arch = st.tabs([
    "🔍  Query", "📋  Schema", "⚙️  Setup", "📜  History", "📊  Evaluation", "⚡  Latency Profile", "🏗️  Architecture"
])


# ─── TAB 1: QUERY ─────────────────────────────────────────────────────────────
with tab_query:
    col_left, col_right = st.columns([3, 2], gap="large")

    with col_left:
        st.markdown('<div class="section-header">Natural Language Question</div>', unsafe_allow_html=True)
        
        # Check if an example was clicked to execute directly
        execute_direct = st.session_state.pop("execute_question", None)
        
        # Determine what to prefill
        if execute_direct:
            prefill = execute_direct
            st.session_state["last_question"] = execute_direct
        else:
            prefill = st.session_state.get("last_question", "")

        question = st.text_area(
            "question",
            value=prefill,
            height=110,
            placeholder="e.g. Rank companies by industry performance sales in AP state...",
            label_visibility="collapsed"
        )
        st.session_state["last_question"] = question

        run_col, copy_col, clear_col = st.columns([3, 1.2, 1])
        with run_col:
            st.markdown('<div class="run-btn">', unsafe_allow_html=True)
            run_clicked = st.button("⚡ Generate & Execute SQL", use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)
        with copy_col:
            if not question:
                button_html = """
                <body style="margin: 0; padding: 0; background: transparent; overflow: hidden;">
                    <button id="copy-btn" style="
                        background: #000000;
                        border: 1px solid #1e2d3d;
                        color: #4a5568;
                        border-radius: 8px;
                        font-weight: 500;
                        width: 100%;
                        height: 38px;
                        cursor: not-allowed;
                        font-family: 'Inter', sans-serif;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        gap: 6px;
                        box-sizing: border-box;
                    " disabled>
                        📋 Copy
                    </button>
                </body>
                """
            else:
                encoded_question = base64.b64encode(question.encode('utf-8')).decode('utf-8')
                button_html = f"""
                <body style="margin: 0; padding: 0; background: transparent; overflow: hidden;">
                    <style>
                        #copy-btn:hover {{
                            border-color: #4a5568 !important;
                            background: #111111 !important;
                        }}
                    </style>
                    <button id="copy-btn" style="
                        background: #000000;
                        border: 1px solid #1e2d3d;
                        color: #e2e8f0;
                        border-radius: 8px;
                        font-weight: 500;
                        width: 100%;
                        height: 38px;
                        cursor: pointer;
                        font-family: 'Inter', sans-serif;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        gap: 6px;
                        transition: all 0.2s;
                        box-sizing: border-box;
                    ">
                        📋 Copy
                    </button>
                    <script>
                        document.getElementById('copy-btn').addEventListener('click', function() {{
                            const textToCopy = decodeURIComponent(escape(atob("{encoded_question}")));
                            navigator.clipboard.writeText(textToCopy).then(function() {{
                                const btn = document.getElementById('copy-btn');
                                btn.innerHTML = '✓ Copied!';
                                btn.style.borderColor = '#22c55e';
                                btn.style.color = '#22c55e';
                                setTimeout(function() {{
                                    btn.innerHTML = '📋 Copy';
                                    btn.style.borderColor = '#1e2d3d';
                                    btn.style.color = '#e2e8f0';
                                }}, 2000);
                            }}).catch(function(err) {{
                                console.error('Could not copy text: ', err);
                            }});
                        }});
                    </script>
                </body>
                """
            components.html(button_html, height=38)
        with clear_col:
            if st.button("Clear", use_container_width=True):
                st.session_state.query_result = None
                st.session_state["last_question"] = ""
                from core.conversational_context import clear_session_context
                clear_session_context(st.session_state.session_id)
                st.rerun()

        # Determine if we should trigger execution
        should_run = run_clicked or (execute_direct is not None)
        run_question = execute_direct if execute_direct else question

        if should_run and run_question and run_question.strip():
            if not st.session_state.schema_loaded:
                st.markdown('<div class="error-box">⚠ Please complete Setup first (load your CSV files).</div>', unsafe_allow_html=True)
            else:
                with st.spinner("Thinking..."):
                    try:
                        for _mod in list(sys.modules.keys()):
                            if _mod == "core" or _mod.startswith("core."):
                                sys.modules.pop(_mod, None)
                        from core.phase6_orchestrator import run_pipeline
                        result = run_pipeline(
                            question=run_question,
                            conn_str=st.session_state.conn_str,
                            model=st.session_state.model,
                            embedder=st.session_state.embedder,
                            schema_path=st.session_state.schema_path,
                            session_id=st.session_state.session_id
                        )
                        st.session_state.query_result = result
                    except Exception as e:
                        st.session_state.query_result = {
                            "success": False, "error": str(e),
                            "sql": "", "complexity": "UNKNOWN",
                            "rows": [], "columns": [], "row_count": 0,
                            "execution_time_ms": 0, "healed": False, "warnings": []
                        }

        # ── Results ──
        result = st.session_state.query_result
        if result:
            complexity = result.get("complexity", "")
            badge_html = f'<span class="badge badge-{complexity}">{complexity}</span>'

            healed_html = ""
            if result.get("healed"):
                healed_html = ' <span class="healed-badge">⚕ self-healed</span>'

            st.markdown(
                f'<div style="margin:0.5rem 0">{badge_html}{healed_html}</div>',
                unsafe_allow_html=True
            )

            if result.get("needs_clarification"):
                st.markdown('<div class="section-header">Clarification Needed</div>', unsafe_allow_html=True)
                st.markdown(
                    f'<div class="info-panel">{result.get("assistant_message", "Please add one more detail.")}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown('<div class="section-header">Generated SQL</div>', unsafe_allow_html=True)
                sql_display = result.get("sql", "-- no query generated")
                if sql_display.startswith("OFF_TOPIC"):
                    sql_display = "-- Out-of-domain / off-topic question"
                st.code(sql_display, language="sql")

            if result.get("success"):
                st.markdown('<div class="section-header">Results</div>', unsafe_allow_html=True)
                cols = result.get("columns", [])
                rows = result.get("rows", [])
                if rows:
                    df = pd.DataFrame(rows, columns=cols)
                    st.dataframe(df, use_container_width=True, height=280)
                    st.download_button(
                        "⬇ Download CSV",
                        df.to_csv(index=False),
                        file_name="nlpsql_result.csv",
                        mime="text/csv"
                    )
                else:
                    st.markdown('<div class="info-panel">Query ran successfully but returned 0 rows.</div>', unsafe_allow_html=True)

                if result.get("warnings"):
                    for w in result["warnings"]:
                        st.markdown(f'<div class="warn-box">⚠ {w}</div>', unsafe_allow_html=True)
            elif not result.get("needs_clarification"):
                err = result.get("error", "Unknown error")
                st.markdown(f'<div class="error-box">✗ {err}</div>', unsafe_allow_html=True)

    with col_right:
        st.markdown('<div class="section-header">Execution Metrics</div>', unsafe_allow_html=True)
        result = st.session_state.query_result
        if result:
            m1, m2 = st.columns(2)
            with m1:
                st.markdown(f"""
                <div class="metric-card">
                  <div class="metric-val">{result.get("row_count", 0)}</div>
                  <div class="metric-lbl">Rows Returned</div>
                </div>""", unsafe_allow_html=True)
            with m2:
                ms = result.get("execution_time_ms", 0)
                sec = ms / 1000.0
                st.markdown(f"""
                <div class="metric-card">
                  <div class="metric-val">{sec:.2f}s</div>
                  <div class="metric-lbl">Total Time</div>
                </div>""", unsafe_allow_html=True)

            st.markdown('<div class="section-header">Pipeline Path</div>', unsafe_allow_html=True)
            st.markdown(f"""
            <div class="info-panel">
              Execution path: <code>{result.get("generation_mode", "unknown")}</code><br>
              Review state: <code>{result.get("review_status", "unreviewed")}</code>
            </div>""", unsafe_allow_html=True)

            logical_plan = result.get("logical_plan")
            if logical_plan:
                with st.expander("Logical Query Plan"):
                    st.json(logical_plan)
            elif result.get("reviewed_sql_reused"):
                st.caption("Approved SQL reused directly. Planner execution was intentionally skipped.")

            st.markdown('<div class="section-header">RAG Context Used</div>', unsafe_allow_html=True)
            st.markdown(f"""
            <div class="info-panel">
              📐 Schema fragments: <code>{result.get("schema_context_used", 0)}</code><br>
              💡 Few-shot examples: <code>{result.get("examples_used", 0)}</code><br>
              🤖 Model: <code>{st.session_state.model}</code>
            </div>""", unsafe_allow_html=True)

            st.markdown('<div class="section-header">Feedback</div>', unsafe_allow_html=True)
            fb1, fb2 = st.columns(2)
            with fb1:
                if st.button("👍 Correct", use_container_width=True):
                    from core.phase6_orchestrator import save_feedback
                    if result and result.get("id"):
                        save_feedback(st.session_state.conn_str, result["id"], 1)
                    st.markdown('<div class="success-box">Thanks! Logged as correct.</div>', unsafe_allow_html=True)
            with fb2:
                if st.button("👎 Wrong", use_container_width=True):
                    from core.phase6_orchestrator import save_feedback
                    if result and result.get("id"):
                        save_feedback(st.session_state.conn_str, result["id"], -1)
                    st.markdown('<div class="error-box">Rejected SQL blocked. The next run will attempt a different candidate.</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="info-panel">Run a query to see metrics here.</div>', unsafe_allow_html=True)



# ─── TAB 2: SCHEMA ────────────────────────────────────────────────────────────
with tab_schema:
    st.markdown("### 📋 Database Schema")
    
    schema_path = Path(st.session_state.schema_path)
    if schema_path.exists():
        try:
            with open(schema_path) as f:
                meta = json.load(f)
            tables = meta.get("tables", [])
            
            if tables:
                # Create a tab for each table
                table_tabs = st.tabs([f"📊 {t['table_name']}" for t in tables])
                for idx, t in enumerate(tables):
                    with table_tabs[idx]:
                        st.markdown(f"**Table Name:** `{t['table_name']}` | **Total Rows:** `{t['total_rows']:,}`")
                        
                        # Prepare columns data frame
                        col_rows = []
                        for col in t.get("columns", []):
                            col_rows.append({
                                "Column Name": col.get("clean_name", ""),
                                "Data Type": col.get("pg_type", ""),
                                "Unique Count": f"{col.get('unique_count', 0):,}",
                                "Null Count": f"{col.get('null_count', 0):,}",
                                "Sample Values": ", ".join([str(v) for v in col.get("sample_values", [])[:5]])
                            })
                        df_cols = pd.DataFrame(col_rows)
                        st.dataframe(df_cols, use_container_width=True, hide_index=True)
                        
                        # DDL Expander
                        with st.expander("Show CREATE TABLE DDL"):
                            st.code(t.get("basic_ddl", ""), language="sql")
            else:
                st.markdown('<div class="info-panel">No tables defined in schema metadata.</div>', unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Failed to load schema metadata: {e}")
    else:
        st.markdown('<div class="warn-box">⚠ No schema metadata found. Complete Setup first to ingest schema.</div>', unsafe_allow_html=True)


# ─── TAB 3: SETUP ─────────────────────────────────────────────────────────────
with tab_setup:
    st.markdown("### ⚙️ System Setup")

    setup_col1, setup_col2 = st.columns(2, gap="large")

    # Lock uploads if in Main DB mode to prevent accidental overwriting of schema/data
    is_main_mode = (db_mode == "Main E-Commerce Database")

    with setup_col1:
        st.markdown('<div class="section-header">Step 1 — Upload CSV Files</div>', unsafe_allow_html=True)
        if is_main_mode:
            st.warning("🔒 CSV uploading is locked in Main E-Commerce mode to protect your production tables from being overwritten. Switch to 'Custom Uploads (upload_db)' in the sidebar to upload files safely.")
        else:
            st.markdown('<div class="info-panel">Upload your custom CSV files. The system will build tables and load data into upload_db.</div>', unsafe_allow_html=True)

        csv1 = st.file_uploader("CSV File 1", type=["csv"], key="csv1", disabled=is_main_mode)
        tbl1 = st.text_input("Table name for File 1", value="table_one", key="tbl1", disabled=is_main_mode)
        csv2 = st.file_uploader("CSV File 2", type=["csv"], key="csv2", disabled=is_main_mode)
        tbl2 = st.text_input("Table name for File 2", value="table_two", key="tbl2", disabled=is_main_mode)
        csv3 = st.file_uploader("CSV File 3", type=["csv"], key="csv3", disabled=is_main_mode)
        tbl3 = st.text_input("Table name for File 3", value="table_three", key="tbl3", disabled=is_main_mode)

        if st.button("🔄 Run Phase 1: Analyze & Import Schema", use_container_width=True, disabled=is_main_mode):
            uploads = [(csv1, tbl1), (csv2, tbl2), (csv3, tbl3)]
            valid = [(f, t) for f, t in uploads if f is not None]
            if not valid:
                st.markdown('<div class="error-box">Upload at least one CSV file.</div>', unsafe_allow_html=True)
            else:
                import psycopg2
                existing_tables = []
                if target_db == "upload_db":
                    try:
                        conn = psycopg2.connect(st.session_state.conn_str)
                        cur = conn.cursor()
                        for _, table_name in valid:
                            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s", (table_name,))
                            if cur.fetchone():
                                existing_tables.append(table_name)
                        cur.close()
                        conn.close()
                    except Exception:
                        pass # DB might not exist yet
                        
                if existing_tables:
                    st.session_state.pending_overwrite_tables = existing_tables
                else:
                    st.session_state.pending_overwrite_tables = []
                    st.session_state.force_ingestion = True

        if st.session_state.get("pending_overwrite_tables"):
            existing_tables = st.session_state.pending_overwrite_tables
            st.markdown(f'<div class="error-box">Warning: Table(s) already exist: <b>{", ".join(existing_tables)}</b>.<br>Please change the table name(s) or click "Overwrite Existing Tables" below to replace them.</div>', unsafe_allow_html=True)
            if st.button("⚠️ Overwrite Existing Tables", use_container_width=True):
                st.session_state.pending_overwrite_tables = []
                st.session_state.force_ingestion = True

        if st.session_state.pop("force_ingestion", False):
            uploads = [(csv1, tbl1), (csv2, tbl2), (csv3, tbl3)]
            valid = [(f, t) for f, t in uploads if f is not None]
            if not valid:
                st.markdown('<div class="error-box">Upload at least one CSV file.</div>', unsafe_allow_html=True)
            else:
                with st.spinner("Analyzing schema..."):
                    from core.phase1_ingestion import analyze_csv, save_schema_metadata, generate_basic_ddl
                    import tempfile
                    import csv
                    import re
                    
                    analyses = []
                    for uploaded_file, table_name in valid:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
                            tmp.write(uploaded_file.read())
                            tmp_path = tmp.name
                        a = analyze_csv(tmp_path, table_name)
                        analyses.append(a)
                        os.unlink(tmp_path)
                    os.makedirs("data", exist_ok=True)
                    save_schema_metadata(analyses, st.session_state.schema_path)
                    st.markdown(f'<div class="success-box">✓ Schema analyzed: {len(analyses)} tables detected.</div>', unsafe_allow_html=True)
                    
                    # If target_db is upload_db, create database and tables, and import data!
                    if target_db == "upload_db":
                        with st.spinner("Importing data rows into upload_db..."):
                            # 1. Create upload_db if it doesn't exist
                            try:
                                default_conn_str = set_db_name_in_url(st.session_state.base_conn_str, "postgres")
                                conn = psycopg2.connect(default_conn_str)
                                conn.autocommit = True
                                cur = conn.cursor()
                                cur.execute("SELECT 1 FROM pg_database WHERE datname='upload_db'")
                                exists = cur.fetchone()
                                if not exists:
                                    cur.execute("CREATE DATABASE upload_db")
                                cur.close()
                                conn.close()
                            except Exception as e:
                                st.markdown(f'<div class="error-box">Failed to create upload_db: {str(e)}</div>', unsafe_allow_html=True)
                                
                            # 2. Connect to upload_db to create tables and import rows!
                            try:
                                upload_conn_str = st.session_state.conn_str
                                conn = psycopg2.connect(upload_conn_str)
                                conn.autocommit = True
                                cur = conn.cursor()
                                
                                for uploaded_file, table_name in valid:
                                    matching_a = next(a for a in analyses if a["table_name"] == table_name)
                                    ddl = generate_basic_ddl(matching_a)
                                    
                                    cur.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE;")
                                    cur.execute(ddl)
                                    
                                    # Copy expert loader (high performance)
                                    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
                                        uploaded_file.seek(0)
                                        tmp.write(uploaded_file.read())
                                        tmp_path = tmp.name
                                        
                                    with open(tmp_path, "r", encoding="utf-8") as f:
                                        reader = csv.reader(f)
                                        header = next(reader)
                                        clean_header = [re.sub(r'[^a-zA-Z0-9_]', '_', c.strip().lower()) for c in header]
                                        cols = ",".join(clean_header)
                                        
                                        f.seek(0)
                                        next(f)
                                        cur.copy_expert(f"COPY {table_name} ({cols}) FROM STDIN WITH CSV", f)
                                    
                                    os.unlink(tmp_path)
                                    
                                    # Verify count
                                    cur.execute(f"SELECT COUNT(*) FROM {table_name}")
                                    rows_count = cur.fetchone()[0]
                                    st.markdown(f'<div class="success-box">✓ Table `{table_name}`: successfully imported {rows_count:,} rows into upload_db.</div>', unsafe_allow_html=True)
                                
                                cur.close()
                                conn.close()
                            except Exception as e:
                                st.markdown(f'<div class="error-box">Failed to import data: {str(e)}</div>', unsafe_allow_html=True)
                    st.rerun()

    with setup_col2:
        st.markdown('<div class="section-header">Step 2 — Initialize Vector Store</div>', unsafe_allow_html=True)
        st.markdown('<div class="info-panel">Creates pgvector tables, embeds schema + examples. Runs 100% offline using local sentence-transformers.</div>', unsafe_allow_html=True)

        # Allow Step 2 for both, but warn if main e-commerce is chosen and they haven't set up
        if st.button("🧠 Run Phase 2: Build Embeddings", use_container_width=True):
            if not schema_path.exists():
                st.markdown('<div class="error-box">Complete Step 1 first.</div>', unsafe_allow_html=True)
            else:
                with st.spinner("Setting up vector store and embedding schema..."):
                    try:
                        from core.phase2_embeddings import (
                            setup_pgvector, embed_and_store_schema, get_local_embedder, embed_and_store_examples
                        )
                        setup_pgvector(st.session_state.conn_str)
                        with open(st.session_state.schema_path) as f:
                            meta = json.load(f)
                        if st.session_state.embedder is None:
                            st.session_state.embedder = get_local_embedder()
                        count = embed_and_store_schema(meta, st.session_state.conn_str, st.session_state.embedder)
                        st.markdown(f'<div class="success-box">✓ Embedded {count} schema fragments into pgvector.</div>', unsafe_allow_html=True)
                        
                        # Dynamically seed RAG examples into the vector store
                        seed_path = Path("seed_pairs.json")
                        if seed_path.exists():
                            with open(seed_path) as sf:
                                examples = json.load(sf)
                            ex_count = embed_and_store_examples(examples, st.session_state.conn_str)
                            st.markdown(f'<div class="success-box">✓ Embedded {ex_count} few-shot examples into vector store.</div>', unsafe_allow_html=True)
                    except Exception as e:
                        st.markdown(f'<div class="error-box">✗ {str(e)}</div>', unsafe_allow_html=True)

        st.markdown('<div class="section-header">Step 3 — Ollama Setup Guide</div>', unsafe_allow_html=True)
        st.markdown("""
        <div class="info-panel">
          1. Install Ollama: <code>https://ollama.ai</code><br>
          2. Start server: <code>ollama serve</code><br>
          3. Pull model: <code>ollama pull sqlcoder</code><br>
          4. Verify: <code>ollama list</code>
        </div>
        """, unsafe_allow_html=True)

        if st.button("🔍 Check Ollama Status", use_container_width=True):
            try:
                import requests
                r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
                models = [m["name"] for m in r.json().get("models", [])]
                st.markdown(f'<div class="success-box">✓ Ollama running · Models: {", ".join(models) or "none pulled"}</div>', unsafe_allow_html=True)
            except Exception:
                st.markdown('<div class="error-box">✗ Ollama not running. Run: <code>ollama serve</code></div>', unsafe_allow_html=True)


# ─── TAB 4: HISTORY ───────────────────────────────────────────────────────────
with tab_history:
    st.markdown("### 📜 Query History")

    if st.button("🔄 Refresh History"):
        st.rerun()

    try:
        from core.phase6_orchestrator import get_query_history
        history = get_query_history(st.session_state.conn_str, limit=100)
        if history:
            df_h = pd.DataFrame(history)
            df_h["status"] = df_h["was_valid"].map({True: "✓", False: "✗"})
            df_h["execution_time_sec"] = df_h["execution_time_ms"] / 1000.0
            cols_show = ["created_at", "complexity", "status", "execution_time_sec", "row_count", "question"]
            df_h_show = df_h[cols_show].rename(columns={
                "created_at": "Time", "complexity": "Complexity",
                "status": "Status", "execution_time_sec": "Seconds",
                "row_count": "Rows", "question": "Question"
            })
            st.dataframe(df_h_show, use_container_width=True, height=400)
        else:
            st.markdown('<div class="info-panel">No queries yet. Run a query from the Query tab.</div>', unsafe_allow_html=True)
    except Exception as e:
        st.markdown(f'<div class="warn-box">Cannot load history: {str(e)[:120]}</div>', unsafe_allow_html=True)


# ─── TAB 5: EVALUATION ────────────────────────────────────────────────────────
with tab_eval:
    import glob
    from datetime import datetime
    from collections import defaultdict
    
    def normalize_report(run_data: dict) -> dict:
        """Normalize old evaluator and new evaluation suite JSON reports to a unified format."""
        summary = run_data.get("summary", {})
        details = run_data.get("details", [])
        
        # 1. Normalize Summary
        norm_summary = {
            "timestamp": summary.get("timestamp", ""),
            "total_queries": summary.get("total_queries", len(details)),
            "intent_accuracy": summary.get("intent_accuracy", 0.0),
            "entity_accuracy": summary.get("entity_accuracy", summary.get("entity_strict_accuracy", 0.0)),
            "planner_accuracy": summary.get("planner_accuracy", None),
            "sql_accuracy": summary.get("sql_accuracy", 0.0),
            "execution_accuracy": summary.get("execution_accuracy", 0.0),
            "business_accuracy": summary.get("business_accuracy", None),
            "average_latency_ms": summary.get("average_latency_ms", 0.0),
        }
        
        # 2. Normalize Details
        norm_details = []
        for item in details:
            # Determine if it's old or new format
            is_new_format = isinstance(item.get("intent"), dict)
            
            if is_new_format:
                norm_item = {
                    "question": item.get("question", ""),
                    "expected_intent": item.get("intent", {}).get("expected", ""),
                    "actual_intent": item.get("intent", {}).get("actual", ""),
                    "intent_match": item.get("intent", {}).get("match", False),
                    "expected_entities": item.get("entities", {}).get("expected", {}),
                    "actual_entities": item.get("entities", {}).get("actual", {}),
                    "entity_strict_match": item.get("entities", {}).get("match", False),
                    "planner_match": item.get("planner", {}).get("match", False),
                    "expected_sql": item.get("sql", {}).get("expected", ""),
                    "generated_sql": item.get("sql", {}).get("generated", ""),
                    "sql_strict_match": item.get("sql", {}).get("match", False),
                    "execution_match": item.get("execution", {}).get("match", False),
                    "business_match": item.get("business", {}).get("match", False),
                    "duration_ms": item.get("latency_ms", 0.0),
                    "error": item.get("error", None)
                }
            else:
                norm_item = {
                    "question": item.get("question", ""),
                    "expected_intent": item.get("expected_intent", ""),
                    "actual_intent": item.get("actual_intent", ""),
                    "intent_match": item.get("intent_match", False),
                    "expected_entities": item.get("expected_entities", {}),
                    "actual_entities": item.get("actual_entities", {}),
                    "entity_strict_match": item.get("entity_strict_match", False),
                    "planner_match": None,
                    "expected_sql": item.get("expected_sql", ""),
                    "generated_sql": item.get("generated_sql", ""),
                    "sql_strict_match": item.get("sql_strict_match", False),
                    "execution_match": item.get("execution_match", False),
                    "business_match": None,
                    "duration_ms": item.get("duration_ms", 0.0),
                    "error": item.get("error", None)
                }
            norm_details.append(norm_item)
            
        return {"summary": norm_summary, "details": norm_details}

    st.markdown("### 📊 Accuracy Evaluation Dashboard")

    dashboard_view = st.radio("Dashboard View", ["Benchmark Analytics", "Custom Gold Tests Manager"], horizontal=True)

    if dashboard_view == "Benchmark Analytics":
        report_files = sorted(glob.glob("benchmark/results/accuracy_report_*.json"), reverse=True)
        
        if not report_files:
            st.info("No benchmark run reports found. Run a new benchmark execution below.")
            if st.button("⚡ Run Full Evaluation Suite (1202 Queries)", use_container_width=True):
                with st.spinner("Running benchmark suite... This may take up to 2 minutes."):
                    try:
                        import subprocess
                        result = subprocess.run([".venv/bin/python", "-m", "benchmark.run_evaluation_suite"], capture_output=True, text=True)
                        st.success("Benchmark completed successfully!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to execute benchmark script: {e}")
        else:
            # Dropdown for selecting a run
            run_choices = {}
            for f in report_files:
                basename = os.path.basename(f)
                ts_str = basename.replace("accuracy_report_", "").replace(".json", "")
                try:
                    dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
                    label = f"Run on {dt.strftime('%B %d, %Y at %I:%M %p')}"
                except Exception:
                    label = f"Run: {ts_str}"
                run_choices[label] = f
                
            selected_label = st.selectbox("Select Accuracy Run", list(run_choices.keys()))
            selected_file = run_choices[selected_label]
            
            with open(selected_file, "r") as f:
                raw_run_data = json.load(f)
                
            run_data = normalize_report(raw_run_data)
            summary = run_data["summary"]
            details = run_data["details"]
            
            # High-level KPIs in clean cards (7 cards for 7 metrics)
            kpi_cols = st.columns(7)
            
            metrics_to_show = [
                ("Intent Accuracy", f"{summary['intent_accuracy']}%", "#6366f1"),
                ("Entity Accuracy", f"{summary['entity_accuracy']}%", "#8b5cf6"),
                ("Planner Accuracy", f"{summary['planner_accuracy']}%" if summary['planner_accuracy'] is not None else "N/A", "#d946ef"),
                ("SQL Accuracy", f"{summary['sql_accuracy']}%", "#f59e0b"),
                ("Execution Accuracy", f"{summary['execution_accuracy']}%", "#10b981"),
                ("Business Accuracy", f"{summary['business_accuracy']}%" if summary['business_accuracy'] is not None else "N/A", "#ec4899"),
                ("Avg Latency", f"{summary['average_latency_ms']} ms", "#06b6d4")
            ]
            
            for idx, (label, val, color) in enumerate(metrics_to_show):
                with kpi_cols[idx]:
                    st.markdown(f"""
                    <div class="metric-card" style="padding: 0.8rem 0.5rem; min-height: 100px;">
                      <div class="metric-val" style="color:{color}; font-size: 1.25rem;">{val}</div>
                      <div class="metric-lbl" style="font-size: 0.62rem; margin-top: 4px;">{label}</div>
                    </div>""", unsafe_allow_html=True)
                
            st.markdown("<br>", unsafe_allow_html=True)
            
            # General parameters
            st.info(f"📂 **Total Benchmark Queries:** {summary['total_queries']} questions")

            # Category Metrics Breakdown Table
            st.markdown('<div class="section-header">Metrics Breakdown by Expected Intent</div>', unsafe_allow_html=True)
            cat_stats = defaultdict(lambda: {
                "total": 0,
                "intent_ok": 0,
                "planner_ok": 0,
                "has_planner": False,
                "sql_ok": 0,
                "exec_ok": 0,
                "business_ok": 0,
                "has_business": False,
                "time_sum": 0.0
            })
            for d in details:
                c = d["expected_intent"]
                cat_stats[c]["total"] += 1
                if d["intent_match"]:
                    cat_stats[c]["intent_ok"] += 1
                if d["planner_match"] is not None:
                    cat_stats[c]["has_planner"] = True
                    if d["planner_match"]:
                        cat_stats[c]["planner_ok"] += 1
                if d["sql_strict_match"]:
                    cat_stats[c]["sql_ok"] += 1
                if d["execution_match"]:
                    cat_stats[c]["exec_ok"] += 1
                if d["business_match"] is not None:
                    cat_stats[c]["has_business"] = True
                    if d["business_match"]:
                        cat_stats[c]["business_ok"] += 1
                cat_stats[c]["time_sum"] += d["duration_ms"]
                
            cat_table = []
            for cat, s in cat_stats.items():
                cat_table.append({
                    "Analytical Intent": cat.upper(),
                    "Queries": s["total"],
                    "Intent Accuracy": f"{(s['intent_ok']/s['total'])*100:.1f}%",
                    "Planner Accuracy": f"{(s['planner_ok']/s['total'])*100:.1f}%" if s['has_planner'] else "N/A",
                    "SQL Match": f"{(s['sql_ok']/s['total'])*100:.1f}%",
                    "Execution Accuracy": f"{(s['exec_ok']/s['total'])*100:.1f}%",
                    "Business Accuracy": f"{(s['business_ok']/s['total'])*100:.1f}%" if s['has_business'] else "N/A",
                    "Avg Latency": f"{s['time_sum']/s['total']:.1f} ms"
                })
            st.dataframe(pd.DataFrame(cat_table), use_container_width=True, hide_index=True)
            
            # Interactive Drilldown List
            st.markdown('<div class="section-header">Query Drilldown & Debugging</div>', unsafe_allow_html=True)
            
            search_col, filter_intent_col, filter_status_col = st.columns([2, 1, 1])
            with search_col:
                q_query = st.text_input("Search prompts...", placeholder="Type question keywords...")
            with filter_intent_col:
                all_intents = ["ALL"] + sorted(list(set(d["expected_intent"] for d in details)))
                sel_intent = st.selectbox("Filter by Intent", all_intents)
            with filter_status_col:
                sel_status = st.selectbox("Filter by Execution", ["ALL", "Passed", "Failed"])
                
            filtered = []
            for d in details:
                if q_query.lower() not in d["question"].lower():
                    continue
                if sel_intent != "ALL" and d["expected_intent"] != sel_intent:
                    continue
                if sel_status == "Passed" and not d["execution_match"]:
                    continue
                if sel_status == "Failed" and d["execution_match"]:
                    continue
                filtered.append(d)
                
            st.markdown(f"Displaying **{len(filtered)}** matching test results:")
            
            for idx, item in enumerate(filtered):
                icon = "🟢" if item["execution_match"] else "🔴"
                lbl = f"{icon} [{item['expected_intent'].upper()}] {item['question']}"
                with st.expander(lbl):
                    dl1, dl2 = st.columns(2)
                    with dl1:
                        st.markdown(f"**Intent Match:** `{item['intent_match']}` (Expected: `{item['expected_intent']}`, Got: `{item['actual_intent']}`)")
                        st.markdown(f"**Entity Match:** `{item['entity_strict_match']}`")
                        if item['planner_match'] is not None:
                            st.markdown(f"**Planner Match:** `{item['planner_match']}`")
                        st.markdown(f"**SQL Strict Match:** `{item['sql_strict_match']}`")
                        st.markdown(f"**Execution Match:** `{item['execution_match']}`")
                        if item['business_match'] is not None:
                            st.markdown(f"**Business Rule Compliant:** `{item['business_match']}`")
                        st.markdown(f"**Latency:** `{item['duration_ms']} ms`")
                        if item["error"]:
                            st.markdown(f"⚠️ **Error:** `{item['error']}`")
                    with dl2:
                        st.markdown("**Expected SQL:**")
                        st.code(item["expected_sql"], language="sql")
                        st.markdown("**Generated SQL:**")
                        st.code(item["generated_sql"] or "-- Pipeline Failed --", language="sql")
                        
            # Live evaluation runner button
            if st.button("⚡ Execute Live Benchmark Run (1202 Queries)", use_container_width=True):
                with st.spinner("Executing benchmark evaluator..."):
                    try:
                        import subprocess
                        result = subprocess.run([".venv/bin/python", "-m", "benchmark.run_evaluation_suite"], capture_output=True, text=True)
                        st.success("Benchmark completed! Reloading dashboard...")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to run: {e}")

    else:
        # Custom gold test suite control (Original functionality)
        st.markdown('<div class="section-header">Custom Gold Test Suite Manager</div>', unsafe_allow_html=True)
        gold_path = Path("data/gold_tests.json")
        tests = []
        if gold_path.exists():
            try:
                tests = json.loads(gold_path.read_text())
            except Exception:
                pass
        
        st.markdown(f'<div class="info-panel">📁 Custom Gold Suite contains <b>{len(tests)}</b> custom test cases.</div>', unsafe_allow_html=True)
        
        if st.button("🧪 Run Custom Gold Suite", use_container_width=True):
            if not tests:
                st.error("No gold test cases found. Add some below first.")
            else:
                with st.spinner("Executing custom test cases..."):
                    try:
                        from core.phase7_evaluation import run_evaluation as run_custom_eval
                        from core.phase6_orchestrator import run_pipeline
                        from core.phase2_embeddings import get_local_embedder

                        if st.session_state.embedder is None:
                            st.session_state.embedder = get_local_embedder()

                        def eval_pipeline_fn(q):
                            return run_pipeline(
                                question=q,
                                conn_str=st.session_state.conn_str,
                                model=st.session_state.model,
                                embedder=st.session_state.embedder,
                                schema_path=st.session_state.schema_path
                            )

                        eval_results = run_custom_eval(
                            conn_str=st.session_state.conn_str,
                            test_cases=tests,
                            pipeline_fn=eval_pipeline_fn
                        )
                        st.session_state.eval_results = eval_results
                    except Exception as e:
                        st.error(f"Evaluation failed: {e}")

        # Display evaluation results if run
        if "eval_results" in st.session_state:
            res = st.session_state.eval_results
            st.markdown('<div class="section-header">Live Evaluation Results</div>', unsafe_allow_html=True)
            
            m1, m2, m3, m4 = st.columns(4)
            with m1:
                st.markdown(f'<div class="metric-card"><div class="metric-val">{res["total_tests"]}</div><div class="metric-lbl">Total Tests</div></div>', unsafe_allow_html=True)
            with m2:
                st.markdown(f'<div class="metric-card"><div class="metric-val" style="color:#22c55e">{res["passed"]}</div><div class="metric-lbl">Passed</div></div>', unsafe_allow_html=True)
            with m3:
                st.markdown(f'<div class="metric-card"><div class="metric-val" style="color:#f87171">{res["failed"]}</div><div class="metric-lbl">Failed</div></div>', unsafe_allow_html=True)
            with m4:
                acc = round(res["average_accuracy"] * 100, 1)
                st.markdown(f'<div class="metric-card"><div class="metric-val">{acc}%</div><div class="metric-lbl">Average Accuracy</div></div>', unsafe_allow_html=True)
                
            # Detailed test results
            st.markdown("#### Test Details")
            detail_data = []
            for r in res["results"]:
                detail_data.append({
                    "Question": r["question"],
                    "Complexity": r["complexity"],
                    "Gold SQL": r["gold_sql"],
                    "Generated SQL": r["generated_sql"],
                    "Score": r["accuracy_score"],
                    "Match": "✓ Pass" if r["passed"] else "✗ Fail"
                })
            st.dataframe(pd.DataFrame(detail_data), use_container_width=True)

        st.markdown('<div class="section-header">Add Gold Test Case</div>', unsafe_allow_html=True)
        gold_q = st.text_input("Natural language question", placeholder="e.g. Rank companies by industry performance sales in AP state.", key="add_gold_q")
        gold_sql = st.text_area("Gold SQL (correct answer)", height=100, placeholder="e.g. SELECT comname, SUM(netweight_tmt) FROM industry_performance WHERE statename = 'ANDHRA PRADESH' GROUP BY comname;", key="add_gold_sql")
        gold_level = st.selectbox("Complexity", ["SIMPLE", "MEDIUM", "HARD", "ADVANCED", "ULTRA"], key="add_gold_level")
        if st.button("Add to Test Suite", key="add_gold_btn"):
            if not gold_q.strip() or not gold_sql.strip():
                st.error("Please provide both the question and the Gold SQL query.")
            else:
                with st.spinner("Adding test case to database and vector memory..."):
                    try:
                        gold_path = Path("data/gold_tests.json")
                        tests = json.loads(gold_path.read_text()) if gold_path.exists() else []
                        tests.append({"question": gold_q, "gold_sql": gold_sql, "complexity": gold_level})
                        gold_path.write_text(json.dumps(tests, indent=2))
                        
                        from core.phase7_evaluation import add_failure_to_examples
                        add_failure_to_examples(
                            conn_str=st.session_state.conn_str,
                            question=gold_q,
                            correct_sql=gold_sql,
                            complexity=gold_level
                        )
                        st.success("Test case successfully added to JSON suite and embedded into the vector database!")
                    except Exception as e:
                        st.error(f"Failed to add to database: {e}")



# ─── TAB 6: PERFORMANCE ────────────────────────────────────────────────────────
with tab_perf:
    st.markdown("### ⚡ Latency & Performance Profile")
    st.markdown("This dashboard displays execution times and latency metrics across the pipeline components.")

    try:
        from core.db_pool import pooled_connect
        import pandas as pd
        
        conn = pooled_connect(DB_URL)
        cur = conn.cursor()
        cur.execute(
            """SELECT latency_info, execution_time_ms, was_valid, question, created_at 
               FROM nlpsql_query_history 
               WHERE latency_info IS NOT NULL 
               ORDER BY id DESC LIMIT 100"""
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        if not rows:
            st.info("No query executions with latency profiles found in database yet. Try executing some natural language queries first!")
        else:
            run_data = []
            for r in rows:
                lat = r[0]
                if isinstance(lat, str):
                    import json
                    try:
                        lat = json.loads(lat)
                    except:
                        continue
                if not lat:
                    continue
                lat["total_duration_ms"] = r[1]
                lat["was_valid"] = r[2]
                lat["question"] = r[3]
                lat["created_at"] = r[4]
                run_data.append(lat)
            
            if run_data:
                df = pd.DataFrame(run_data)
                
                avg_total = df["total_duration_ms"].mean()
                valid_rate = df["was_valid"].mean() * 100
                
                c1, c2, c3 = st.columns(3)
                c1.metric("Avg Pipeline Duration", f"{avg_total:.1f} ms")
                c2.metric("Valid Execution Rate", f"{valid_rate:.1f} %")
                c3.metric("Total Tracked Runs", f"{len(df)}")
                
                st.markdown("#### Average Stage Latencies")
                
                stage_cols = [
                    "router_ms", "entity_extraction_ms", "retrieval_ms", 
                    "bm25_ms", "pgvector_ms", "reranker_ms", 
                    "llm_ms", "validator_ms", "db_ms"
                ]
                
                avgs = df[stage_cols].mean().round(2)
                
                try:
                    import plotly.express as px
                    chart_df = pd.DataFrame({
                        "Stage": [s.replace("_ms", "").replace("_", " ").title() for s in stage_cols],
                        "Avg Latency (ms)": [avgs[s] for s in stage_cols]
                    })
                    fig = px.bar(
                        chart_df, 
                        x="Avg Latency (ms)", 
                        y="Stage", 
                        orientation="h",
                        text="Avg Latency (ms)",
                        color="Avg Latency (ms)",
                        color_continuous_scale="Viridis",
                        title="Average Component Execution Latency"
                    )
                    fig.update_layout(yaxis={'categoryorder':'total ascending'})
                    st.plotly_chart(fig, use_container_width=True)
                except Exception:
                    st.bar_chart(pd.DataFrame({
                        "Avg Latency (ms)": [avgs[s] for s in stage_cols]
                    }, index=[s.replace("_ms", "").replace("_", " ").title() for s in stage_cols]))
                
                st.markdown("#### Recent Executions Profiling Details")
                st.dataframe(
                    df[["created_at", "question", "total_duration_ms", "was_valid"] + stage_cols]
                    .rename(columns={
                        "created_at": "Time",
                        "question": "Question",
                        "total_duration_ms": "Total (ms)",
                        "was_valid": "Valid?"
                    })
                )
    except Exception as e:
        st.error(f"Failed to load performance metrics: {e}")


# ─── TAB 7: ARCHITECTURE ──────────────────────────────────────────────────────
with tab_arch:
    st.markdown("### 🏗️ System Architecture")
    
    arch_path = Path("architecture.svg")
    if arch_path.exists():
        st.markdown('<div class="info-panel">This diagram illustrates the multi-phase autonomous NL2SQL pipeline architecture.</div>', unsafe_allow_html=True)
        # Display SVG
        import base64
        with open(arch_path, "rb") as f:
            base64_svg = base64.b64encode(f.read()).decode("utf-8")
        svg_uri = f"data:image/svg+xml;base64,{base64_svg}"
        st.markdown(f'<div style="background:white; padding:20px; border-radius:10px; text-align:center;"><img src="{svg_uri}" style="max-width: 100%;"></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="warn-box">⚠ Architecture diagram (nl_to_sql_architecture.svg) not found in the project root.</div>', unsafe_allow_html=True)
        st.info("The diagram should be located at the root of the project folder.")
