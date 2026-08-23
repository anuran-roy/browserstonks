# BrowserStonks CLI

BrowserStonks automates repeated form-filling. Integrations (the Chrome
extension is the first one) send the current page/DOM state to this CLI;
the CLI consults its rulebook of saved answers (or asks an LLM provider to
write one) and responds with a **Python program** the integration executes
inside Pyodide/WASM on the page, plus the values to fill in.

## Architecture

    [integration (e.g. Chrome extension)]
        |  sends: DOM snapshot / form description
        v
    [PluginManager]  <- dynamic, pluggable transports (stdio, http, websocket, ...)
        |
    [FormPlanner]    <- site adapters (Google Forms, Typeform, generic HTML forms)
        |
    [LLM Providers]  <- OpenRouter / OpenAI / Anthropic / custom, writes the program
        |
    [MCP tools]      <- optional Model Context Protocol servers from config.jsonc

    Response: { program: "<python source>", values: {...} }

## Quick start

    pip install -e .
    browserstonks init          # writes config.jsonc, config.schema.jsonc, .env
    browserstonks serve         # start all enabled integrations (default: ws on 8765)
    browserstonks doctor        # validate config, env keys, MCP servers

## Config

`config.jsonc` (JSON with comments) holds LLM provider keys, integration
settings, and MCP server definitions. `.env` is loaded before integrations
start; `config.jsonc` values support `${VAR}` expansion from the environment.
`config.schema.jsonc` documents every allowed field.

## Adding integrations

Any Python package exposing `browserstonks_integrations` (a dict of
`name -> Integration subclass`) is auto-discovered. Built-ins live in
`browserstonks/integrations/`. See `INTEGRATIONS.md`.
