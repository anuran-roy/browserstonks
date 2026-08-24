# BrowserStonks Integrations

An *integration* is a transport that connects an external client to the
BrowserStonks planner and chat pipeline. The Chrome extension is the first one; the CLI's
plugin system treats it as just another plugin.

## Wire protocol (all transports)

### Form filling

Request (client → CLI):

    {
      "type": "plan",
      "requestId": "req_1",          // echoed back
      "sender": "chrome-extension",   // client id
      "dom": {
        "url": "https://…",
        "site": "google_forms",       // optional hint; auto-detected otherwise
        "forms": [
          {
            "id": "form1",
            "fields": [
              {"id": "q1", "label": "Your name", "type": "text",
               "required": true, "options": [], "selector": "#q1 input",
               "value": ""}          // existing content — never overwritten
            ]
          }
        ]
      }
    }

Response (CLI → client):

    {
      "program": "def fill(page, VALUES): ...",   // run in Pyodide on the page
      "values":  {"your name": "Anuran", ...},    // answers to fill
      "site":    "google_forms",
      "requestId": "req_1", "tabId": 42           // routing echo
    }

The client executes the program with `page` (set/click/select/submit
helpers) and `VALUES` available as Python globals — via Pyodide in an
offscreen document.

### Chat (agent harnesses)

Request:

    {
      "type": "chat", "harness": "opencode", "sessionId": "primary",
      "message": "research X and write a report", "requestId": "chat_1"
    }

The CLI runs a multi-round work loop: the harness's action lines are
executed (`tab.*`, `doc.create`), read/process results are fed back as
ACTION RESULTS turns, browser events are flushed mid-loop, and the loop
continues until the harness replies without read/process actions (max
16 rounds by default — `ChatHandler(max_rounds=…)`).

Streaming during the turn (CLI → client, before the final response):

    {"type": "chat.progress", "requestId": "chat_1",
     "partType": "reasoning" | "text", "text": "<delta>"}

Mid-loop browser events (CLI → client, applied immediately):

    {"type": "browser.events", "events": [{"type": "tab.new", "url": …}]}

Final response:

    {
      "type": "chat.response",
      "reply": "…markdown…",
      "actionsApplied": [{"op": "tab.read", "ok": true}, …],
      "events": [ …remaining browser events… ],
      "meta": {"reasoning": "…", "session": "ses_…"}
    }

### Session tracking

| message | direction | purpose |
|---|---|---|
| `session.hello` | client → CLI | full snapshot: profile, windows, tabs |
| `session.tab.updated` / `session.tab.removed` / `session.window.removed` | client → CLI | deltas from tab lifecycle events |
| `session.snapshot` | client → CLI | query the CLI's tracked state |
| `session.ack` | CLI → client | quiet ack for session events |

### Tab reading (harness eyes)

    CLI → client: {"type": "tab.read.request", "tabId": 42, "requestId": "…"}
    client → CLI: {"type": "tab.read.response", "requestId": "…", "html": "…"}

The client's content script returns `document.documentElement.outerHTML`
for the tab. Authenticated pages work — the read happens in the user's
browser with their sessions.

### Utility

- `{"type": "ping"}` → `{"type": "pong"}` (the extension pings every 20s
  to keep the MV3 worker alive; the websocket server's protocol keepalive
  is disabled by design)
- `{"type": "harnesses.list"}` → available harnesses

## Harness action vocabulary

Every harness turn is prefixed with a shared system prompt
(`prompt.py`) teaching these action lines:

    [[tab.new|<url>]]                 open a tab (alias: tab.open)
    [[tab.close|<tabIdOrUrl>]]        close a tab
    [[tab.read|<tabIdOrUrl>]]         read a tab's HTML (results fed back)
    [[tab.process|<tabIdOrUrl>]]      run harness-written process(HTML) code
                                      (fenced ```python block; stdlib; 30s
                                      timeout; JSON result fed back)
    [[doc.create|<format>|<file>]]    create html/markdown/text docs

The prompt also forbids bash/curl for browser actions (pages must be read
through the user's browser for login state) and instructs the model to
chain reads until the task is complete.

## Built-in integrations

| name       | transport                          | config key                |
|------------|------------------------------------|---------------------------|
| websocket  | ws:// server (Chrome extension)    | integrations.websocket    |
| stdio      | line-delimited JSON on stdin/stdout| integrations.stdio        |

## Adding a new integration

**Drop-in (no code changes):** create `plugins/my_transport.py` in your
config root:

    from browserstonks.integrations import BaseIntegration

    class MyTransport(BaseIntegration):
        name = "my_transport"
        async def start(self):
            ...  # listen however you like; call self.handle(dom) per request

    ENTRYPOINTS = {"browserstonks_integrations": {"my_transport": MyTransport}}

Enable it in config.jsonc: `"integrations": {"my_transport": {"enabled": true}}`.

**As a package:** expose the `browserstonks_integrations` entry-point
group in your pyproject and it will be auto-discovered.

## Form adapters (plug-n-play sites)

`generic` (plain HTML forms), `google_forms`, `typeform` ship built-in.
Add new sites the same way — `browserstonks_form_adapters` entry-point
group, or a drop-in file in `plugins/`. Detection is by URL hostname, or
the client can send `"site": "..."`.

## Harnesses

Built-in: `opencode` (HTTP server with SSE streaming + async prompts +
tool auto-approval, or local binary), `openclaw` (gateway `/tools/invoke`
with bearer auth), `hermes` (CLI). Extend via the
`browserstonks_harnesses` group. Harness `send()` may accept an optional
`on_progress({"type", "text"})` callback to stream live model output.

Server auth per harness via the `auth` block: bearer / basic / apikey
(custom header or query param) / extra headers — see
`config.schema.jsonc`.

## Custom MCP servers

config.jsonc → `mcpServers` (stdio JSON-RPC, MCP spec). Servers are
started with `browserstonks serve`; their tools are listed via
`MCPManager.tool_catalog()` and callable with `MCPManager.call()`.
