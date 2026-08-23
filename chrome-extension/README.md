# BrowserStonks Chrome Extension

First integration for the BrowserStonks CLI. It:

1. Snapshots the current page's form DOM state (labels, types, options,
   selectors — understands Google Forms, Typeform, and plain HTML forms).
2. Sends the snapshot to the CLI over WebSocket (`ws://127.0.0.1:8765`).
3. Receives a Python program + values, loads **Pyodide** (CPython compiled
   to WebAssembly) into the page, and executes `fill(page, VALUES)` where
   `page` is a helper bound to the live DOM (set / click / select / submit).

## Install (Chrome, dev mode)

1. `chrome://extensions` → enable **Developer mode**
2. **Load unpacked** → select this folder
3. Start the CLI:
   ```
   cd ../CLI
   pip install -e .
   browserstonks init && browserstonks serve
   ```
4. Open any form (e.g. a Google Form), click the BrowserStonks icon → **Fill this form**

## Files

- `manifest.json` — MV3 manifest
- `background.js` — service worker: WebSocket to CLI, message routing
- `content.js` — DOM snapshot + Pyodide loader + `page` helper
- `popup.html` / `popup.js` — fill button, CLI URL setting

## Notes

- Pyodide (~10MB WASM) is fetched from jsDelivr on first fill and cached
  by the browser; it boots once per page and is reused.
- The CLI's `answers.jsonc` rulebook supplies the values; with an LLM
  provider configured, the CLI writes a custom program per form.
- Other clients (no Chrome involved) can use the same WebSocket protocol
  or the `stdio` integration — see ../CLI/INTEGRATIONS.md.
