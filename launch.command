#!/bin/zsh
set -e

cd -- "$(dirname -- "$0")"

if ! curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
  nohup ollama serve >/private/tmp/ollama-sales-offline.log 2>&1 &
  sleep 2
fi

exec .venv/bin/streamlit run app.py --server.port 8501 --server.headless true
