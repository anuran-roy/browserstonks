# BrowserStonks

Automate form filling for repeated questions.

```
chrome-extension/     Chrome MV3 extension (first plugin for the CLI)
CLI/                  Python CLI: planner, LLM providers, plugin system, MCP
```

## How it works

1. The Chrome extension snapshots the current page's form DOM state.
2. It sends the snapshot to the CLI over WebSocket.
3. The CLI normalizes it via a form adapter (Google Forms, Typeform,
   generic HTML — plug-n-play), picks values from the `answers.jsonc`
   rulebook, and — when a rule doesn't cover the form — asks the
   configured LLM (OpenRouter / OpenAI / Anthropic / offline) to write a
   small Python program for it.
4. The CLI replies with `{program, values}`.
5. The extension loads Pyodide (CPython in WebAssembly) into the page and
   runs `fill(page, VALUES)`, which fills the form through a `page`
   helper bound to the live DOM.

## Layout

- `CLI/browserstonks/` — the package
  - `cli.py` — `init` / `serve` / `plan` / `doctor` commands
  - `config.py` — JSONC config loader, `.env` loader, `${VAR}` expansion,
    schema validation against `config.schema.jsonc`
  - `plugins.py` — dynamic plugin registry (local `plugins/` dir +
    package entry-points + built-ins)
  - `integrations/` — transports (websocket, stdio)
  - `form_adapters.py` — site adapters (generic, google_forms, typeform)
  - `planner.py` — rulebook + LLM program generation
  - `llm.py` — provider clients (OpenRouter, OpenAI, Anthropic)
  - `mcp.py` — custom MCP server support from config.jsonc
- `chrome-extension/` — MV3 extension (background service worker,
  content script with Pyodide injection, popup)

## Quick start

```
cd CLI
pip install -e .
browserstonks init        # config.jsonc, config.schema.jsonc, .env, answers.jsonc
# put your API key in .env, answers in answers.jsonc
browserstonks serve
```

Then load `chrome-extension/` as an unpacked extension and click
**Fill this form** on any form page.

Verified: `doctor` validates config against the schema; a live WebSocket
round-trip (Google Forms snapshot → program + values → executed
`fill()`) passes end to end.
