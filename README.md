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
  - **OpenCode** — local binary or HTTP server (verified protocol)
  - **OpenClaw** — gateway `/tools/invoke` API with bearer auth
  - **Hermes** — via the `hermes` CLI
  - any other harness via the `browserstonks_harnesses` plugin group
- Harnesses reply in markdown and can emit actions:
  `[[tab.new|https://…]]`, `[[doc.create|markdown|notes.md]]`, …
- The CLI applies actions: opens/closes real tabs, writes HTML/markdown/text docs to `docs/` and opens them

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

### Custom MCP servers
Declare Model Context Protocol servers in `config.jsonc` (`mcpServers`); launched over stdio JSON-RPC, tools listed and callable by the planner.

### Config & validation
- `config.jsonc` — JSON-with-comments config; `${VAR}` env expansion
- `config.schema.jsonc` — schema of every allowed field; `serve` auto-syncs it with the packaged schema; `doctor` validates
- `.env` — loaded before integrations start (see `.env.example`)

### Headless / CI-ready (by design)
The event bus records browser events when no browser is attached, so the same action pipeline can run headless and be asserted on later — the foundation for CI/CD automation without a display.

## Quick start

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
