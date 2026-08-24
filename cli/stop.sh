#!/usr/bin/env bash
# stop.sh — stop a daemonized BrowserStonks CLI server started by start.sh.
set -euo pipefail
cd "$(dirname "$0")"

PORT=8765
stopped=0

if [ -f serve.pid ]; then
  PID=$(cat serve.pid)
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" && echo "stopped pid $PID"
    stopped=1
  fi
  rm -f serve.pid
fi

# catch any stray serve on the port
STRAY=$(lsof -t -iTCP:$PORT -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$STRAY" ]; then
  kill $STRAY 2>/dev/null || true
  echo "stopped stray pid(s): $STRAY"
  stopped=1
fi

[ "$stopped" = 1 ] || echo "nothing to stop"
