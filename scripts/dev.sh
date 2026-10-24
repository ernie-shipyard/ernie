#!/usr/bin/env bash
# One command for the whole app: backend (FastAPI on :8000), UI (Vite on :5173) and the Electron window.
# Electron starts uvicorn itself if nothing is on :8000; run `uvicorn app:app --reload` yourself for hot reload.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -x .venv/bin/uvicorn ] || { echo "No .venv. Run: python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
[ -f .env ] || { echo "No .env. Run: cp .env.example .env, then add ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID"; exit 1; }
command -v ffmpeg >/dev/null || echo "warning: ffmpeg not on PATH, hum-to-MIDI will fail (brew install ffmpeg)"
[ -d ernie-ui/node_modules ] || (cd ernie-ui && npm ci)

# Clear servers left over from an earlier run of this repo (Ctrl-C doesn't always reach them). Anything else on
# these ports is left alone and reported.
for port in 8000 5173; do
  for pid in $(lsof -tiTCP:$port -sTCP:LISTEN 2>/dev/null); do
    if ps -o command= -p "$pid" | grep -q "$PWD"; then
      echo "stopping stale Ernie server on :$port (pid $pid)"; kill "$pid"
    else
      echo "port $port is used by another program: $(ps -o command= -p "$pid")"; exit 1
    fi
  done
done
sleep 0.5

cd ernie-ui && exec npm run dev:electron
