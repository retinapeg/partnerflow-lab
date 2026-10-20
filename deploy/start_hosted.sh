#!/usr/bin/env sh
# Hosted entrypoint: API on a private port, dashboard on the public port.
# Render sets $PORT; Hugging Face Spaces uses 7860; default 7860.
set -e
export PARTNERFLOW_API_PORT="${PARTNERFLOW_API_PORT:-8010}"
export PARTNERFLOW_API_URL="http://127.0.0.1:${PARTNERFLOW_API_PORT}"
PUBLIC_PORT="${PORT:-7860}"

python -m uvicorn main:app --host 127.0.0.1 --port "$PARTNERFLOW_API_PORT" &
API_PID=$!

# Wait for the API before starting the dashboard.
for i in $(seq 1 60); do
  if python -c "import httpx,sys; sys.exit(0 if httpx.get('$PARTNERFLOW_API_URL/health', timeout=2).status_code==200 else 1)" 2>/dev/null; then break; fi
  sleep 1
done

exec python -m streamlit run dashboard.py \
  --server.port "$PUBLIC_PORT" --server.address 0.0.0.0 \
  --server.headless true --browser.gatherUsageStats false \
  --server.enableCORS false --server.enableXsrfProtection false
