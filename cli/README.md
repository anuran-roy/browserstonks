# BrowserStonks CLI

BrowserStonks turns any Chromium-based browser into an **agentic
browser**. Integrations (the Chrome extension is the first one) connect
the browser to this CLI, which provides:

- **Agentic form filling** — form detection, answer resolution
  (rulebook → LLM → mock), and Python programs executed on the page via
  Pyodide (WASM) by the integration. Pre-filled fields are never
  overwritten.
- **Session tracking** — a coherent model of windows/tabs/profiles,
  kept in sync with the live browser, fed to agent prompts.
- **Agent-harness chat** — the sidepanel talks to coding-agent harnesses
  (OpenCode, OpenClaw, Hermes) whose replies drive the browser:
  open/close/read tabs, run Python against page HTML, and create
  documents (HTML/markdown/text). Long turns stream the model's
  reasoning live; multi-step research chains across many read rounds.

## Architecture

    [integration (e.g. Chrome extension)]
        |  plan / chat / session.* / tab.read.* over the transport
        v
    [PluginManager]  <- dynamic transports (stdio, websocket, ...)
        |
    [FormPlanner]    <- site adapters (Google Forms, Typeform, generic)
        |            <- answers: rulebook -> LLM (OpenRouter/OpenAI/Anthropic)
    [SessionTracker] <- windows/tabs/profiles, kept coherent
    [EventBus]       <- tab.new / doc.open events applied by the browser
    [ChatHandler]    <- harness turns -> multi-round work loop
        |               (actions executed, results fed back, events flushed)
    [HarnessManager] <- OpenCode (SSE streaming) / OpenClaw / Hermes
        |
    [MCP tools]      <- optional Model Context Protocol servers from config.jsonc

## Quick start

One-command setup (installs CLI + harnesses, builds the sidepanel,
auto-configures, validates, optionally starts):

    ./setup.sh                    # everything
    ./setup.sh --no-start         # don't start the server
    ./setup.sh --skip-harnesses   # skip OpenCode/OpenClaw/Hermes installs

`setup.sh` auto-detects a running OpenCode server (`:4096`), reads the
OpenClaw gateway token from `~/.openclaw/openclaw.json`, and wires the
Hermes CLI — dropping the right entries into `config.jsonc`. Existing
config files are never overwritten (a `.jsonc.bak` backup is kept).

Manual alternative:

    pip install -e .
    browserstonks init          # writes config.jsonc, config.schema.jsonc, .env
    browserstonks serve         # start all enabled integrations (default: ws on 8765)
    browserstonks doctor        # validate config, env keys, MCP servers

Server lifecycle: `./start.sh` (foreground), `./start.sh --daemon`
(logs to serve.log), `./stop.sh`. Log detail with `BS_VERBOSE=1` /
`BS_DEBUG=1` or `--verbose` / `--debug`.

## Logging

Every subsystem logs through named loggers (`ws`, `chat`, `harness`,
`actions`, `plan`, `answers`, `mcp`, `plugins`, `config`), so you can
follow a request across every hop with timestamps:

    17:37:06.535 INFO  [chat]    turn start opencode/primary (message 42 chars)
    17:37:06.536 INFO  [harness] opencode SSE event stream connected
    17:37:06.910 INFO  [actions] tab.new https://example.com -> event queued
    17:37:06.911 INFO  [chat]    turn complete opencode/primary: reply 120 chars, 1 action(s)

Levels: `INFO` = one line per pipeline step (default); `DEBUG` adds
payloads (generated programs, HTTP bodies, answer sources); `WARN`/`ERROR`
cover failures and degradations. Control with:

- `browserstonks serve --verbose` / `--debug`
- `BROWSERSTONKS_LOG=DEBUG` in the environment or `.env`
- `BS_VERBOSE=1` / `BS_DEBUG=1` with `./start.sh`

## Secure internet mode & managed storage

- **Connection tokens**: `browserstonks tokens issue --label <name>`
  (prints the plaintext once; SHA-256 hash stored). `revoke` and `list`
  manage them. With `"auth": { "required": true }` in config.jsonc the
  websocket server requires `{"type":"auth","token":"…"}` as the first
  message — the extension's **Secure auth** checkbox sends it. Token DB:
  SQLite (`tokens.db`) by default; `auth.database` accepts
  `postgres://…` or `mongodb://…` DSNs (drivers optional).
- **Managed storage**: `"storage": { "enabled": true, "provider":
  "cloudinary" }` (or `s3` / `gcs`, credentials in `.env`) uploads every
  generated doc after the local `media/<sessionId>/` write;
  `doc.open` then points at the served URL.

## Config

`config.jsonc` (JSON with comments) holds LLM provider keys, harness
endpoints + auth (bearer/basic/apikey/headers), integration settings,
and MCP server definitions. `.env` is loaded before integrations start;
config values support `${VAR}` expansion from the environment.
`config.schema.jsonc` documents every allowed field (auto-synced by
`serve`).

## Harnesses

- **OpenCode** — server mode (`base_url`) uses the real HTTP API: async
  prompts, SSE event streaming (`/event`) for live thinking deltas,
  session busy/idle for turn completion, and automatic tool-permission
  approval (`autoApproveTools`: true once / "always" / false). Local
  binary mode also supported. `turnTimeout` (default 600s) and
  `pollInterval` are configurable.
- **OpenClaw** — gateway `/tools/invoke` API (`sessions_send` +
  `sessions_history` polling), bearer-token auth.
- **Hermes** — driven through the `hermes` CLI (`hermes -z`).

Any harness via the `browserstonks_harnesses` plugin group; streaming
harnesses implement `send(..., on_progress=…)`.

## Chat modes (plan / build)

Every chat turn runs in a mode set by the client (the sidepanel header
toggle), default `plan`:

- **plan** — read-only: `tab.new/open/close/read/process/focus`. For
  research and exploration; the model is instructed to clean up tabs it
  opened.
- **build** — plan + `doc.create/modify/delete` (docs in
  `media/<sessionId>/`) + page actions: `tab.action` (fill/click/select/
  submit), `tab.edit` (DOM edits per selector), `tab.submit` (submits the
  form closest to the last edited element).

Actions outside the mode's allowed set are filtered and reported as failed.
The mode rides the chat message (`{"mode": "build"}`), never config.jsonc.

## Sessions & persistence

- Chat transcripts persist per session under `sessions/` (JSON, one file
  per session: messages, actions, reasoning, title = first user message).
- `sessions.list` / `session.load` / `session.delete` over the websocket.
- The sidepanel remembers its last active session (localStorage pointer)
  and reloads the transcript when reopened — conversations survive panel
  close/reopen and browser restarts.

## Adding integrations / plugins

Any Python package exposing `browserstonks_integrations` (a dict of
`name -> Integration subclass`) is auto-discovered, as are
`browserstonks_form_adapters` and `browserstonks_harnesses`. Drop-in
`.py` files in `plugins/` work too. See `INTEGRATIONS.md` for the full
wire protocol.
