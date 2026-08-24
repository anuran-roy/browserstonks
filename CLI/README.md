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
