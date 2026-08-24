# BrowserStonks CLI

BrowserStonks turns any Chromium-based browser into an **agentic
browser**. Integrations (the Chrome extension is the first one) connect
the browser to this CLI, which provides:

- **Agentic form filling** — form detection, answer resolution
  (rulebook → LLM → mock), and Python programs executed on the page via
  Pyodide (WASM) by the integration.
- **Session tracking** — a coherent model of windows/tabs/profiles,
  kept in sync with the live browser, fed to agent prompts.
- **Agent-harness chat** — the sidepanel talks to coding-agent harnesses
  (OpenCode, OpenClaw, Hermes) whose replies can open tabs, browse, and
  create documents (HTML/markdown/text) through CLI-emitted browser
  events (`tab.new`, `doc.open`, …).

## Architecture

    [integration (e.g. Chrome extension)]
        |  plan / chat / session.* messages over the transport
        v
    [PluginManager]  <- dynamic transports (stdio, http, websocket, ...)
        |
    [FormPlanner]    <- site adapters (Google Forms, Typeform, generic)
        |            <- answers: rulebook -> LLM (OpenRouter/OpenAI/Anthropic)
    [SessionTracker] <- windows/tabs/profiles, kept coherent
    [EventBus]       <- tab.new / doc.open events applied by the browser
    [ChatHandler]    <- harness turns -> actions (browser ops, doc creation)
        |
    [HarnessManager] <- OpenCode / OpenClaw / Hermes (+ plugin harnesses)
        |
    [MCP tools]      <- optional Model Context Protocol servers from config.jsonc

## Quick start

    pip install -e .
    browserstonks init          # writes config.jsonc, config.schema.jsonc, .env
    browserstonks serve         # start all enabled integrations (default: ws on 8765)
    browserstonks doctor        # validate config, env keys, MCP servers

Or use the scripts: `./start.sh` (foreground), `./start.sh --daemon`
(logs to serve.log), `./stop.sh`. Set `BS_VERBOSE=1` or `BS_DEBUG=1`
before start.sh to control log detail.

## Logging

Every subsystem logs through named loggers (`ws`, `chat`, `harness`,
`actions`, `plan`, `answers`, `mcp`, `plugins`, `config`), so you can
follow a request across every hop with timestamps:

    17:37:06.535 INFO  [chat]    turn start opencode/primary (message 42 chars)
    17:37:06.536 INFO  [harness] opencode server POST /session -> 1.2KB (0.3s)
    17:37:06.910 INFO  [actions] tab.new https://example.com -> event queued
    17:37:06.911 INFO  [chat]    turn complete opencode/primary: reply 120 chars, 1 action(s)

Levels: `INFO` = one line per pipeline step (default); `DEBUG` adds
payloads (generated programs, HTTP bodies, answer sources); `WARN`/`ERROR`
cover failures and degradations. Control with:

- `browserstonks serve --verbose` / `--debug`
- `BROWSERSTONKS_LOG=DEBUG` in the environment or `.env`
- `BS_VERBOSE=1` / `BS_DEBUG=1` with `./start.sh`

## Config

`config.jsonc` (JSON with comments) holds LLM provider keys, harness
endpoints + auth, integration settings, and MCP server definitions.
`.env` is loaded before integrations start; config values support
`${VAR}` expansion from the environment. `config.schema.jsonc` documents
every allowed field (auto-synced by `serve`).

## Adding integrations

Any Python package exposing `browserstonks_integrations` (a dict of
`name -> Integration subclass`) is auto-discovered, as are
`browserstonks_form_adapters` and `browserstonks_harnesses`. Drop-in
`.py` files in `plugins/` work too. See `INTEGRATIONS.md`.
