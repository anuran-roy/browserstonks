#!/usr/bin/env bash
# start.sh — run the BrowserStonks CLI server for the Chrome extension.
# Does NOT touch .env (or any config file); only starts things.
#
# Usage:
#   ./start.sh            # foreground (Ctrl-C to stop)
#   ./start.sh --daemon   # background, logs to serve.log, PID in serve.pid

set -euo pipefail
cd "$(dirname "$0")"

PY=python3
HOST=127.0.0.1
PORT=8765
# verbosity: set BS_VERBOSE=1 (INFO) or BS_DEBUG=1 (payloads) before start.sh
EXTRA_FLAGS=()
[ "${BS_VERBOSE:-0}" = "1" ] && EXTRA_FLAGS+=(--verbose)
[ "${BS_DEBUG:-0}" = "1" ] && EXTRA_FLAGS+=(--debug)

# pick a python that has the deps (websockets) available
if ! "$PY" -c "import websockets" 2>/dev/null; then
  for cand in /usr/local/bin/python3 /opt/homebrew/bin/python3 \
              /Library/Frameworks/Python.framework/Versions/3.12/bin/python3; do
    if "$cand" -c "import websockets" 2>/dev/null; then PY="$cand"; break; fi
  done
fi

echo "BrowserStonks CLI"
echo "  python : $($PY --version 2>&1) ($PY)"

# first-run convenience: create config files if missing (skips existing ones,
# .env is only created if it does not exist at all — never modified)
if [ ! -f config.jsonc ]; then
  echo "  config : not found, running 'init' (existing files are skipped)…"
  "$PY" -m browserstonks.cli --root . init
fi

if "$PY" -m browserstonks.cli --root . doctor >/dev/null 2>&1; then
  echo "  config : valid"
else
  echo "  WARNING: doctor reported issues:"
  "$PY" -m browserstonks.cli --root . doctor || true
fi

# refuse to start if the port is already taken (likely a previous serve)
if lsof -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo "  ERROR: port $PORT already in use — is 'serve' already running?"
  echo "  Stop it with: kill \$(lsof -t -iTCP:$PORT -sTCP:LISTEN)"
  exit 1
fi

if [ "${1:-}" = "--daemon" ]; then
  nohup "$PY" -m browserstonks.cli --root . "${EXTRA_FLAGS[@]}" serve > serve.log 2>&1 &
  echo $! > serve.pid
  echo "  mode   : daemon (pid $(cat serve.pid), log: serve.log)"
  for i in $(seq 1 25); do
    if lsof -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
      echo ""
      echo "Listening on ws://$HOST:$PORT — Chrome extension can connect now."
      echo "Stop with: ./stop.sh  (or kill \$(cat serve.pid))"
      exit 0
    fi
    sleep 0.2
  done
  echo "  server did not come up; check serve.log"
  exit 1
else
  echo "  mode   : foreground (Ctrl-C to stop)"
  echo ""
  echo "Listening on ws://$HOST:$PORT — Chrome extension can connect now."
  exec "$PY" -m browserstonks.cli --root . "${EXTRA_FLAGS[@]}" serve
fi
