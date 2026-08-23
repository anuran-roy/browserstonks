// BrowserStonks offscreen document: runs Pyodide (bundled, no CSP issues).
// MV3 service workers can't do DOM/WASM well and extension code may not
// load remote scripts, so Python executes here — the page helper calls
// are relayed back to the content script in the target tab.

const PYODIDE_ROOT = "vendor/pyodide";

let pyodidePromise = null;

function log(...args) {
  console.log("[browserstonks:offscreen]", ...args);
}

async function loadPyodideOnce() {
  if (pyodidePromise) return pyodidePromise;
  pyodidePromise = (async () => {
    try {
      await import(chrome.runtime.getURL(`${PYODIDE_ROOT}/pyodide.js`));
      log("booting pyodide (wasm)…");
      const py = await globalThis.loadPyodide({
        indexURL: chrome.runtime.getURL(`${PYODIDE_ROOT}/`),
      });
      log("pyodide ready");
      return py;
    } catch (e) {
      pyodidePromise = null; // allow retry
      throw e;
    }
  })();
  return pyodidePromise;
}

// Proxy `page` helper calls back to the content script in the target tab.
// page.set(selector, value) here becomes PAGE_CALL {op:"set", args:[...]}
// handled in content.js against the live DOM.
async function runProgram(msg) {
  const py = await loadPyodideOnce();
  const pending = []; // JS promises for in-flight page calls

const pageProxy = {
    set: (sel, val, force) => relayPageCall(msg.tabId, "set", [sel, val, !!force], pending),
    click: (sel) => relayPageCall(msg.tabId, "click", [sel], pending),
    select: (sel, val) => relayPageCall(msg.tabId, "select", [sel, val], pending),
    submit: () => relayPageCall(msg.tabId, "submit", [], pending),
  };

  function relayPageCall(tabId, op, args, pending) {
    // Offscreen documents have no chrome.tabs access — route through the
    // background service worker via chrome.runtime, which forwards the
    // PAGE_CALL to the content script in the target tab.
    const send = () => chrome.runtime.sendMessage({
      type: "PAGE_CALL_RELAY", tabId, op, args,
    });
    const p = Promise.resolve().then(send).then((res) => {
      if (res && res.error) throw new Error(res.error);
      return res?.result;
    }).catch((e) => {
      throw new Error(`page.${op} failed: ${e.message}`);
    });
    pending.push(p);
    return p;
  }

  py.globals.set("page", pageProxy);
  // VALUES crosses the JS/Python boundary as a JSON string
  py.globals.set("VALUES_JSON", JSON.stringify(msg.values || {}));
  py.runPython("import json\nVALUES = json.loads(VALUES_JSON)");

  try {
    py.runPython(msg.program);
    py.runPython("fill(page, VALUES)");
    // wait for any page-call promises the program kicked off
    await Promise.all(pending);
    log("program finished");
    return { ok: true };
  } catch (e) {
    log("program error:", e.message || String(e));
    return { ok: false, error: String(e.message || e) };
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "RUN_PROGRAM_OFFSCREEN") {
    runProgram(msg)
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true; // async
  }
});

log("offscreen runtime ready");
