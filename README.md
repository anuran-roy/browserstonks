# BrowserStonks

<p align="center"><img src="./assets/browserstonks.png" height="400px"/></p>

Turn any Chromium-based browser into an **agentic browser** — a Chrome extension + CLI system where AI agent harnesses can see the browser (form detection, tab/window/profile session tracking), act on it (fill forms, open tabs, create docs), and chat with you from a sidepanel.

What began as form-filling automation is now a general browser-agent platform: the Chrome extension is the *first* integration for the CLI, and the CLI is the pluggable brain (LLMs, agent harnesses, MCP tools) behind any browser client.

## Architecture

```
┌──────────────────────── Chromium browser ────────────────────────┐
│  extension: content script      background service worker        │
│    DOM/form snapshots  ────────►  WebSocket ⇄ CLI                │
│    page ops (set/click/…)        sidepanel chat (streamdown UI)  │
│                                  offscreen doc: Pyodide (WASM)   │
└──────────────────────────────┬───────────────────────────────────┘
                               │ ws://127.0.0.1:8765
┌──────────────────────────────▼─────────────── CLI (Python) ─────┐
│  integrations: websocket, stdio (+ plugin transports)           │
│  form adapters: generic, Google Forms, Typeform (+ plugins)     │
│  planner: rulebook answers + LLM-generated fill programs        │
│  session tracker: windows / tabs / profiles, kept coherent      │
│  event bus: tab.new / tab.open / tab.close / doc.open           │
│  chat handler: harness turns → actions (browser ops, docs)      │
│  harnesses: OpenCode, OpenClaw, Hermes (+ plugin harnesses)     │
│  LLM providers: OpenRouter, OpenAI, Anthropic, offline          │
│  MCP servers: custom Model Context Protocol tools (stdio RPC)   │
└─────────────────────────────────────────────────────────────────┘
```

## Features

### Agentic form filling
- Detect forms on any page (generic HTML; plug-n-play adapters for Google Forms, Typeform, and more via the plugin system)
- Snapshot labels, types, options, selectors, and *existing values* — pre-filled fields are never overwritten
- Answers resolved per field: rulebook (`answers.jsonc`) → LLM → mock
- The CLI writes a small **Python program** per form; the extension executes it in **Pyodide** (CPython compiled to WebAssembly) in an offscreen document and applies `page.set/click/select/submit` against the live DOM

### Sidepanel chat (agent harnesses)
- React + **Vercel streamdown** chat UI (bundled, CSP-safe, no CDN)
- Talk to coding-agent harnesses that can drive the browser:
  - **OpenCode** — local binary or HTTP server with **live SSE streaming** (thinking + text deltas as they're generated), async prompts (no HTTP timeouts on long turns), multi-turn memory, and automatic tool-permission approval
  - **OpenClaw** — gateway `/tools/invoke` API with bearer auth
  - **Hermes** — via the `hermes` CLI
  - any other harness via the `browserstonks_harnesses` plugin group
- Harnesses reply in markdown and can emit actions:
  `[[tab.new|https://…]]`, `[[doc.create|markdown|notes.md]]`, …
- The CLI applies actions: opens/closes real tabs, writes HTML/markdown/text docs to `media/<sessionId>/` and opens them — optionally publishing to managed storage (Cloudinary default, S3, GCS) and opening the served URL
- **Live model thinking** — the model's reasoning stream renders in the panel while it works (💭 thinking); the final message keeps a collapsible full-reasoning section
- **Stop button** — terminate any running turn instantly (aborts the harness-side run too, e.g. OpenCode session abort); hung responses can't freeze the UI (guaranteed `chat.completed` signal + hard turn timeout + client safety nets)
- **Session history** — every conversation is persisted under `cli/sessions/`; a picker below the harness dropdown lists saved sessions (title + message count), restores transcripts, and continues where you left off
- **Multi-round work loop** — harnesses chain `tab.read`/`tab.process` actions until the whole task is done (open → read → analyze → more reads → final report), without needing "Continue" prompts

### Reading & processing pages (the harness's eyes)
- `tab.read` — harness reads any open tab's HTML through *your* browser (login state, sessions, everything)
- `tab.process` — harness writes its own `process(HTML)` Python (stdlib, 30s timeout) and gets the result back
- Read/process failures are fed back so the model can adapt (open the tab, then retry)

### Interactive action badges (link previews)
Every action badge (`tab.new ✓`, `tab.read ✕`, `doc.create ✓`…) is interactive:
- **Hover** — a rich Google-search-style preview card: favicon, hostname, page title, Open Graph description/site name/image — judge how legit a referenced site is before visiting
- **Click** — jump to the exact tab that action opened (or reopen the URL if it's gone)
- Failed badges show their error inline; successful reads show chars read

### Browser session awareness
- The extension reports a full session snapshot on connect and deltas on every tab/window change
- The CLI keeps a coherent model: profiles → windows → tabs (url, title, active) — queryable via `session.snapshot`, fed to harness prompts
- CLI-emitted events (`tab.new`, `tab.open`, `tab.close`, `doc.open`) are applied by the extension in real time

### Dynamic plugin system (everything is pluggable)
Three plugin families, discovered from built-ins, drop-in `.py` files in `plugins/`, and package entry-points:
- `browserstonks_integrations` — transports (websocket, stdio built-in)
- `browserstonks_form_adapters` — form-builder site adapters
- `browserstonks_harnesses` — agent harnesses

### LLM providers
OpenRouter, OpenAI (and any OpenAI-compatible base_url), Anthropic, or offline (rulebook only). Keys via `.env` with `${VAR}` expansion in `config.jsonc`.

### Harness server authentication
Per-harness `auth` block in `config.jsonc`: bearer, basic, apikey (custom header or query param), plus arbitrary extra headers — for any secure server-mode harness.

### Secure internet mode (connection tokens)
Expose the CLI beyond localhost safely:
- Issue tokens: `browserstonks tokens issue --label work-laptop` (plaintext shown once; SHA-256 hash stored), `revoke` / `list` to manage
- With `auth.required: true`, every websocket connection must authenticate with a valid token as its first message — invalid/missing tokens are dropped at the gate
- Token database: **SQLite** by default (`tokens.db`); `auth.database` accepts `postgres://…` / `mongodb://…` DSNs for shared DBs
- Extension side: **Secure auth** checkbox in the popup reveals a token field; the background worker sends it on every connect/reconnect

### Managed storage for generated content
- `doc.create` always writes locally to `media/<sessionId>/`; with `storage.enabled: true` it **also uploads** and the `doc.open` action opens the hosted URL
- Providers: **Cloudinary** (default), **Amazon S3**, **Google Cloud Storage** — all via raw HTTP, no SDKs; credentials in `.env`

### Custom MCP servers
Declare Model Context Protocol servers in `config.jsonc` (`mcpServers`); launched over stdio JSON-RPC, tools listed and callable by the planner.

### Config & validation
- `config.jsonc` — JSON-with-comments config; `${VAR}` env expansion
- `config.schema.jsonc` — schema of every allowed field; `serve` auto-syncs it with the packaged schema; `doctor` validates
- `.env` — loaded before integrations start (see `.env.example`)

### Structured logging
Named subsystem loggers (`ws`, `chat`, `harness`, `actions`, `plan`, `answers`, `mcp`, `plugins`, `auth`, `storage`, `sessions`), one INFO line per pipeline step, DEBUG for payloads; `--verbose`/`--debug`, `BROWSERSTONKS_LOG`, or `BS_VERBOSE`/`BS_DEBUG` with `start.sh`.

### Headless / CI-ready (by design)
The event bus records browser events when no browser is attached, so the same action pipeline can run headless and be asserted on later — the foundation for CI/CD automation without a display.

## Quick start

**Secure internet mode** — expose the CLI beyond localhost safely:
issue connection tokens (`browserstonks tokens issue --label my-laptop`),
enable `"auth": { "required": true }` in config.jsonc (token DB: SQLite by
default; Postgres/Mongo DSN supported), and turn on **Secure auth** in the
extension popup, pasting the token. Connections without a valid token are
refused. Enable `"storage": { "enabled": true, "provider": "cloudinary" }`
(with credentials in `.env`) to publish generated docs online — the
`doc.open` action then opens the hosted URL.

One-command setup — installs the CLI, the agent harnesses (OpenCode,
OpenClaw, Hermes), builds the extension sidepanel, auto-configures
`config.jsonc` (detecting running servers and reading the OpenClaw
token from `~/.openclaw`), validates with `doctor`, and starts serving:

```
cd cli
./setup.sh                    # install + configure + start
./setup.sh --no-start         # install + configure only
./setup.sh --skip-harnesses   # CLI + extension only
```

Manual alternative:

```
cd cli
pip install -e .
browserstonks init            # config.jsonc, config.schema.jsonc, .env, answers.jsonc
# put LLM_API_KEY in .env; configure harnesses in config.jsonc
./start.sh                    # or ./start.sh --daemon
```

Load `chrome-extension/` as an unpacked extension (`chrome://extensions` → Developer mode → Load unpacked), open any form, click **Fill this form**, or open the sidepanel and chat with a harness.

## CLI commands

```
browserstonks init      # write config files (never overwrites)
browserstonks serve     # start enabled integrations + MCP + harnesses
browserstonks plan      # one-shot: DOM snapshot JSON -> fill program
browserstonks doctor    # validate config/env/keys/harnesses/mcp
```

## Wire protocol (integrations)

| message | direction | purpose |
|---|---|---|
| `{type:"plan", dom, requestId}` | client → CLI | form-fill plan (program + values) |
| `{type:"chat", harness, sessionId, message}` | client → CLI | one agent turn |
| `{type:"session.hello", profile, windows}` | client → CLI | full session snapshot |
| `{type:"session.tab.updated", ...}` etc. | client → CLI | session deltas |
| `{type:"harnesses.list"}` | client → CLI | available harnesses |
| `{type:"session.snapshot"}` | client → CLI | query tracked state |
| `{type:"chat.response", reply, actionsApplied, events}` | CLI → client | chat reply + applied actions + browser events |
| `{type:"browser.events", events}` | CLI → client | events to apply (tab.new, doc.open, …) |

See `cli/INTEGRATIONS.md` for the full protocol and plugin authoring
guide.

## Repo layout

```
chrome-extension/        MV3 extension
  manifest.json          permissions, sidepanel, CSP (wasm-unsafe-eval)
  background.js          websocket client, event application, session reporting
  content.js             DOM/form snapshot, page ops
  offscreen.js/.html     Pyodide (bundled WASM) program execution
  sidepanel-src/         React + streamdown chat source (esbuild → sidepanel.js)
  popup.html/.js         fill button, chat button, CLI URL setting
  vendor/pyodide/        bundled Pyodide runtime
cli/                     Python CLI
  browserstonks/
    cli.py               init/serve/plan/doctor
    config.py            JSONC + .env loading, ${VAR} expansion, validation
    config.schema.jsonc  allowed config fields
    plugins.py           dynamic plugin registry (3 families)
    integrations/        websocket + stdio transports, message dispatch
    form_adapters.py     generic / Google Forms / Typeform
    planner.py           fill-program generation (rulebook + LLM)
    answers.py           per-field answer resolution (async get_answer)
    prompt.py            shared harness system prompt (action vocabulary)
    logging_util.py      named loggers, levels, BROWSERSTONKS_LOG
    session.py           SessionTracker + EventBus
    chat.py              chat handler, work loop, action routing, docs
    harnesses.py         OpenCode (SSE) / OpenClaw / Hermes + auth + sessions
    llm.py               provider clients
    mcp.py               MCP server support
  plugins/               drop-in plugin directory
  setup.sh               one-command install: CLI + harnesses + extension build
  start.sh / stop.sh     server lifecycle scripts (BS_VERBOSE/BS_DEBUG)
```

## Status

- [x] Verified end-to-end against live services: the full plan/fill pipeline, chat → action → browser-event round-trips, and multi-round research turns (open tabs, read HTML through the user's browser, chained reads, doc creation)
- [x] OpenCode server mode fully verified live: SSE thinking streams, multi-turn memory, tool-use turns with auto-approved permissions
- [x] OpenClaw gateway verified at transport level; Hermes is CLI-driven (provider-limited).

## Changelog

### 2026-08-25 — Secure internet mode and content publishing
- **Connection-token auth**: `browserstonks tokens issue/revoke/list`; SHA-256-hashed tokens in SQLite (default) / Postgres / Mongo; websocket auth gate (`auth.required`) closes unauthenticated connections; extension **Secure auth** checkbox + token field in the popup
- **Managed storage**: `doc.create` uploads to **Cloudinary (default) / S3 / GCS** after the local write (raw HTTP, no SDKs); `doc.open` opens the hosted URL
- Generated content moved to `media/<sessionId>/` (per-session folders); `cli/docs/` unignored and reserved for developer documentation
- Fix: `tokens issue` stdout polluted by log lines (broke copy-pasted tokens); server now strips token whitespace

### 2026-08-24 (late) — Chat panel UX
- **Interactive action badges**: hover for a rich link preview (favicon, title, Open Graph description/site/image — legit-check before visiting); click jumps to the exact tab that action opened; failed badges show errors inline
- **Session history**: transcripts persisted to `cli/sessions/`; session picker below the harness dropdown (new / load / delete); restores messages, actions, and reasoning
- `tab.read`/`tab.process` badges carry resolved URLs (success and failure) so every reference is previewable
- Raised websocket frame limit to 50 MB; extension truncates oversized page HTML at 2 MiB (with truncation flag) — fixed `1009 message too big` disconnects on large pages
- Fix: empty reply on tool-using turns — completion now keyed on session busy/idle, not the first assistant finish event

### 2026-08-24 — Agentic loop hardening
- **`chat.completed` guarantee**: exactly one completion signal per turn (ok/error/timeout/stopped) plus a hard turn timeout — the UI can never hang
- **Stop button**: kills a running turn instantly, end to end (stop events through the chat handler, SSE wait loop, and OpenCode `/session/{id}/abort`); chat turns run as concurrent tasks so stop/pings are read mid-turn
- **Multi-round work loop**: harnesses chain reads until the task is complete (max 16 rounds); browser events flush mid-loop (tab.new takes effect before the next round); failures feed back so the model can retry
- **OpenCode SSE streaming**: `/event` subscription streams thinking + text deltas live into the panel; async prompts eliminate HTTP timeouts; tool permissions auto-approved; prompt forbids bash/curl for browser actions
- **Structured logging** across every CLI subsystem with `--verbose`/`--debug` and env controls
- MV3 keepalive (20s ping), protocol keepalive disabled, send serialization, graceful disconnects — long turns survive Chrome's worker suspension
- Connection-state notifications in the panel; `setup.sh` one-command installer; absolute doc paths; repo folder renamed `CLI/` to `cli/`

### 2026-08-24 (early) — Foundation
- Chrome extension: form snapshots, bundled Pyodide in an offscreen document (CSP-safe), on-demand content-script injection, fill programs with pre-filled-field protection
- CLI: plugin system (integrations / form adapters / harnesses), LLM providers, MCP servers, JSONC config with schema validation, websocket + stdio transports
- Sidepanel chat (React + streamdown), harness action vocabulary (`tab.*`, `doc.create`), session tracking, event bus
- Verified live against OpenCode (:4096), OpenClaw (:18789), Hermes (:9119)
