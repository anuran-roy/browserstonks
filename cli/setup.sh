#!/usr/bin/env bash
# setup.sh — install BrowserStonks: CLI + agent harnesses (OpenCode, OpenClaw,
# Hermes) + browser extension build, then configure and start.
#
# Usage:
#   ./setup.sh              # everything (install + config + start)
#   ./setup.sh --no-start   # install + config only
#   ./setup.sh --skip-harnesses   # CLI + extension only
#
# Existing configs are NEVER overwritten (init skips existing files).
set -euo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; GREEN=$'\033[32m'; DIM=$'\033[2m'; RESET=$'\033[0m'
step() { echo "${BOLD}${GREEN}==>${RESET} $1"; }
info() { echo "    $1"; }

NO_START=0
SKIP_HARNESS=0
for arg in "$@"; do
  case "$arg" in
    --no-start) NO_START=1 ;;
    --skip-harnesses) SKIP_HARNESS=1 ;;
  esac
done

# ---------------------------------------------------------------- CLI install
step "Installing BrowserStonks CLI"
PY=python3
if ! "$PY" -c "import websockets" 2>/dev/null; then
  for cand in /usr/local/bin/python3 /opt/homebrew/bin/python3 \
              /Library/Frameworks/Python.framework/Versions/3.12/bin/python3; do
    if "$cand" -c "import websockets" 2>/dev/null; then PY="$cand"; break; fi
  done
fi
info "using $($PY --version 2>&1)"
"$PY" -m pip install -q -e . 2>&1 | grep -v "already satisfied" || true
"$PY" -m browserstonks.cli --version

# ---------------------------------------------------------------- harnesses
if [ "$SKIP_HARNESS" = "0" ]; then
  step "Installing agent harnesses (skip with --skip-harnesses)"

  # OpenCode
  if command -v opencode >/dev/null 2>&1; then
    info "opencode: already installed ($(opencode --version 2>/dev/null | head -1))"
  else
    info "opencode: installing via curl installer"
    curl -fsSL https://opencode.ai/install | bash || {
      info "opencode installer failed — install manually from https://opencode.ai"
    }
    # installer may drop the binary in ~/.opencode/bin
    export PATH="$HOME/.opencode/bin:$HOME/.local/bin:$PATH"
    command -v opencode >/dev/null 2>&1 \
      && info "opencode: $(opencode --version 2>/dev/null | head -1)" \
      || info "opencode: not on PATH; server mode can still be used via base_url"
  fi

  # OpenClaw
  if command -v openclaw >/dev/null 2>&1; then
    info "openclaw: already installed"
  else
    info "openclaw: installing via npm"
    npm install -g openclaw 2>/dev/null || {
      info "openclaw: npm install failed — see https://docs.openclaw.ai"
    }
  fi

  # Hermes
  if command -v hermes >/dev/null 2>&1; then
    info "hermes: already installed ($(hermes --version 2>/dev/null | head -1))"
  else
    info "hermes: attempting install (hermes update || pipx || pip)"
    hermes update >/dev/null 2>&1 || pipx install hermes-agent 2>/dev/null \
      || "$PY" -m pip install -q hermes-agent 2>/dev/null \
      || info "hermes: auto-install unavailable — install from https://hermes-agent.nousresearch.com"
  fi
else
  step "Skipping harness installs (--skip-harnesses)"
fi

# ---------------------------------------------------------------- extension build
step "Building Chrome extension sidepanel"
if command -v npm >/dev/null 2>&1 && [ -d ../chrome-extension/sidepanel-src ]; then
  (cd ../chrome-extension/sidepanel-src && npm install --silent && npm run build >/dev/null 2>&1) \
    && info "sidepanel bundled -> ../chrome-extension/sidepanel.js" \
    || info "sidepanel build skipped (npm failed); a prebuilt bundle may exist"
else
  info "npm not found or source missing — using prebuilt sidepanel.js"
fi

# ---------------------------------------------------------------- config
step "Writing config (existing files are never overwritten)"
"$PY" -m browserstonks.cli --root . init

# enable the harnesses that are actually available
"$PY" - <<'EOF'
from pathlib import Path
from browserstonks.config import loads_jsonc, strip_comments
import shutil, json, os

cfg_path = Path("config.jsonc")
cfg = loads_jsonc(cfg_path.read_text())
harnesses = cfg.setdefault("harnesses", {})

# OpenCode: prefer a local server if one is running; otherwise local binary
import urllib.request
try:
    urllib.request.urlopen("http://127.0.0.1:4096/doc", timeout=2)
    harnesses["opencode"] = {"base_url": "http://127.0.0.1:4096"}
    print("    opencode: server detected on :4096 -> server mode")
except Exception:
    import shutil as sh
    if sh.which("opencode"):
        harnesses["opencode"] = {"command": "opencode"}
        print("    opencode: local binary mode")
    else:
        harnesses["opencode"] = {"base_url": "http://127.0.0.1:4096"}
        print("    opencode: defaulting to server base_url :4096")

# OpenClaw: read the gateway token from ~/.openclaw if present
oc_cfg = Path.home() / ".openclaw" / "openclaw.json"
if oc_cfg.exists():
    try:
        token = (json.loads(oc_cfg.read_text()).get("gateway", {})
                 .get("auth", {}).get("token", ""))
    except Exception:
        token = ""
    entry = {"base_url": "http://127.0.0.1:18789"}
    if token:
        entry["auth"] = {"type": "bearer", "token": token}
    harnesses["openclaw"] = entry
    print("    openclaw: gateway :18789" + (" (token from ~/.openclaw)" if token else ""))
else:
    harnesses.setdefault("openclaw", {})
    print("    openclaw: no ~/.openclaw config; placeholder entry")

# Hermes: CLI mode if binary present
if shutil.which("hermes"):
    harnesses["hermes"] = {"base_url": "http://127.0.0.1:9119"}
    print("    hermes: CLI mode (server :9119 noted for future use)")
else:
    harnesses.setdefault("hermes", {})
    print("    hermes: not installed; placeholder entry")

# write back as plain JSON with a header note (comments preserved via schema file)
backup = cfg_path.with_suffix(".jsonc.bak")
if not backup.exists():
    shutil.copy(cfg_path, backup)
cfg_path.write_text(
    "// BrowserStonks configuration (auto-configured by setup.sh; edit freely).\n"
    "// Schema: config.schema.jsonc · values support ${VAR} expansion from .env\n"
    + json.dumps(cfg, indent=2) + "\n")
print("    config.jsonc updated")
EOF

# .env from example if absent
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  info "created .env from .env.example — add your LLM_API_KEY"
fi

# ---------------------------------------------------------------- doctor
step "Validating setup"
"$PY" -m browserstonks.cli --root . doctor || true

# ---------------------------------------------------------------- start
if [ "$NO_START" = "1" ]; then
  step "Done (--no-start). Start the CLI with: ./start.sh"
else
  step "Starting the CLI"
  exec ./start.sh
fi
