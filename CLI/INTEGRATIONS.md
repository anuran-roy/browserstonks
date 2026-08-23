# BrowserStonks Integrations

An *integration* is a transport that connects an external client to the
BrowserStonks planner. The Chrome extension is the first one; the CLI's
plugin system treats it as just another plugin.

## Wire protocol (all transports)

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
               "required": true, "options": [], "selector": "#q1 input"}
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
helpers) and `VALUES` available as Python globals — e.g. via Pyodide.

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
group, or a drop-in file in `plugins/` registering under that group.
Detection is by URL hostname, or the client can send `"site": "..."`.

## Custom MCP servers

config.jsonc → `mcpServers` (stdio JSON-RPC, MCP spec). Servers are
started with `browserstonks serve`; their tools are listed via
`MCPManager.tool_catalog()` and callable with `MCPManager.call()`.
