# BrowserStonks Chrome Extension

First integration for the BrowserStonks CLI: it turns Chromium into an
**agentic browser**. It:

1. Snapshots the current page's form DOM state (labels, types, options,
   selectors, existing values — understands Google Forms, Typeform, and
   plain HTML forms).
2. Sends the snapshot to the CLI over WebSocket (`ws://127.0.0.1:8765`).
3. Receives a Python program + values, loads **Pyodide** (CPython compiled
   to WebAssembly) in an offscreen document, and executes
   `fill(page, VALUES)` where `page` is a helper relayed to the live DOM
   (set / click / select / submit; never overwrites pre-filled fields).
4. Hosts a sidepanel chat (React + Vercel streamdown) wired to agent
   harnesses via the CLI — harnesses can open tabs, read page HTML
   through the user's browser, run Python over it, and create docs via
   emitted browser events. Long turns stream the model's reasoning live
   (💭 thinking), and connection drops/reconnects show inline in the chat.
   Reopening the panel resumes the last active chat session
   (localStorage pointer + transcript reload from the CLI store).
   A plan/build mode toggle in the header sets whether the agent is
   read-only (plan) or can create docs and act on pages (build).
   Long turns stream the model's thinking and text **interleaved** in
   arrival order (not mashed into one thinking block).
   (localStorage pointer + transcript reload from the CLI store).
5. Shows a floating BrowserStonks-logo button on the right edge of every
   page — one click opens the chat panel from any webpage (no toolbar
   needed); falls back to a popup window when Chrome blocks the
   side-panel gesture.
5. Reports browser sessions (windows / tabs / profiles) to the CLI so
   harnesses always see coherent browser state; CLI-emitted events
   (`tab.new`, `tab.close`, `doc.open`) are applied in real time.

## Install (Chrome, dev mode)

1. `chrome://extensions` → enable **Developer mode**
2. **Load unpacked** → select this folder
3. Start the CLI:
   ```
   cd ../cli
   ./setup.sh        # or: pip install -e . && browserstonks init && ./start.sh
   ```
4. Open any form (e.g. a Google Form), click the BrowserStonks icon → **Fill this form**
5. Or click **Open chat panel** and talk to an agent harness

## Files

- `manifest.json` — MV3 manifest (sidePanel, offscreen, wasm-unsafe-eval CSP)
- `background.js` — service worker: WebSocket client with 20s keepalive
  (MV3 workers idle-kill otherwise), message routing, browser-event
  application, session reporting, connection-state broadcasts
- `content.js` — DOM/form snapshot, page ops, tab HTML reads
- `offscreen.html` / `offscreen.js` — Pyodide (bundled WASM) program
  execution; page calls relayed through the background worker
- `sidepanel-src/` — React + streamdown chat source (`npm run build` →
  `sidepanel.js`); live thinking stream, action chips, connection
  notifications
- `popup.html` / `popup.js` — fill button, chat button, CLI URL setting
- `vendor/pyodide/` — bundled Pyodide runtime (no CDN, CSP-safe)

## Notes

- Pyodide (~10MB WASM) is bundled — first fill boots it once per page.
- The CLI's `answers.jsonc` rulebook supplies form values; with an LLM
  provider configured, the CLI writes a custom program per form.
- Other clients (no Chrome involved) can use the same WebSocket protocol
  or the `stdio` integration — see ../cli/INTEGRATIONS.md.
