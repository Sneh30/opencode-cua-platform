#!/usr/bin/env bash
# CUA Control — one-command start for the Computer-Use Platform frontend.
#
#   ./start.sh            start the control server (builds the web UI first
#                         if it has never been built), then open the browser
#   ./start.sh --no-open  same, without opening the browser
#   ./start.sh --build-only
#
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8787}"

if [[ "${1:-}" == "--build-only" ]]; then
  (cd frontend && npm run build)
  exit 0
fi

# Build once if dist is missing
if [[ ! -f frontend/dist/index.html ]]; then
  echo "→ building web UI (first run)"
  (cd frontend && npm install >/dev/null && npm run build)
fi

if [[ "${1:-}" != "--no-open" ]]; then
  sleep 2
  (open "http://localhost:${PORT}" >/dev/null 2>&1 &)
fi

echo "→ starting CUA Control Server on http://localhost:${PORT}"
echo "  · opencode serve + CUA Gateway + Browser Adapter are spawned automatically"
echo "  · press Ctrl-C to stop everything"
cd frontend && exec node server/index.js
