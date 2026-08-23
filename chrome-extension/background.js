// BrowserStonks background service worker.
// Keeps the WebSocket to the BrowserStonks CLI, snapshots the DOM state
// of the active tab on request, and relays the CLI's program to the
// content script for Pyodide execution.

const CLI_WS_URL = () => browserstonksGetConfig().then(c => c.wsUrl);

const DEFAULT_CONFIG = {
  wsUrl: "ws://127.0.0.1:8765",
  autoConnect: true,
};

let ws = null;
let pending = [];      // queued messages while disconnected
const msgIdSeq = { n: 0 };

async function browserstonksGetConfig() {
  const stored = await chrome.storage.local.get("config");
  return { ...DEFAULT_CONFIG, ...(stored.config || {}) };
}

function log(...args) {
  console.log("[browserstonks]", ...args);
}

// Status surfaced without DevTools: badge on the toolbar icon + a line
// in the page console (via the content script) so the user always sees
// what the pipeline is doing.
function badge(text, color = "#4f46e5") {
  chrome.action.setBadgeText({ text: text || "" });
  chrome.action.setBadgeBackgroundColor({ color });
}

async function pageLog(tabId, message) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "STATUS_LOG", message });
  } catch { /* content script may be gone; ignore */ }
}

// ---------------------------------------------------------------- websocket
function connect() {
  return CLI_WS_URL().then((url) => {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    log("connecting to", url);
    ws = new WebSocket(url);
    ws.onopen = () => {
      log("connected to CLI");
      while (pending.length) {
        const m = pending.shift();
        try {
          ws.send(JSON.stringify(m));
          log("flushed queued", m.type, m.requestId || "");
          if (m.__resolve) m.__resolve();
        } catch (e) {
          log("flush failed:", e.message);
        }
      }
    };
    ws.onclose = () => {
      log("CLI disconnected; will retry on next action");
      ws = null;
    };
    ws.onerror = () => { /* onclose follows */ };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch {
        log("unparseable message from CLI:", ev.data?.slice(0, 200));
        return;
      }
      log("message from CLI:", JSON.stringify(msg).slice(0, 300));
      if (msg.error) {
        log("CLI returned an error:", msg.error);
        badge("ERR", "#dc2626");
        pageLog(msg.tabId, `[browserstonks] CLI error: ${msg.error}`);
        return;
      }
      if (msg.requestId && msg.sender && msg.tabId !== undefined && msg.program) {
        // forward the CLI's plan to the offscreen document, which runs
        // Pyodide (bundled WASM) and relays page calls to the content script
        log("routing plan", msg.requestId, "to offscreen (tab", msg.tabId + ")…");
        badge("RUN");
        pageLog(msg.tabId, `[browserstonks] program received (${Object.keys(msg.values || {}).length} values); running Pyodide…`);
        runProgramInOffscreen(msg).then((result) => {
          log("fill result:", JSON.stringify(result));
          const ok = result && result.ok;
          badge(ok ? "OK" : "ERR", ok ? "#16a34a" : "#dc2626");
          pageLog(msg.tabId, `[browserstonks] fill ${ok ? "succeeded" : "failed"}${result?.error ? ": " + result.error : ""}`);
          setTimeout(() => badge(""), 8000);
        }).catch((e) => {
          log("fill failed:", e.message);
          badge("ERR", "#dc2626");
          pageLog(msg.tabId, `[browserstonks] fill failed: ${e.message}`);
        });
      } else {
        log("message from CLI ignored (missing routing fields):",
            msg.requestId, msg.sender, msg.tabId, !!msg.program);
      }
    };
  });
}

// ---------------------------------------------------------------- offscreen
let offscreenCreating = null;

async function ensureOffscreenDocument() {
  const existing = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
  });
  if (existing.length > 0) return;
  if (offscreenCreating) return offscreenCreating;
  offscreenCreating = chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["DOM_SCRAPING"],
    justification: "Runs Pyodide (Python/WASM) to execute CLI-generated form-filling programs",
  });
  try {
    await offscreenCreating;
  } finally {
    offscreenCreating = null;
  }
}

async function runProgramInOffscreen(plan) {
  await ensureOffscreenDocument();
  return chrome.runtime.sendMessage({
    type: "RUN_PROGRAM_OFFSCREEN",
    tabId: plan.tabId,
    program: plan.program,
    values: plan.values,
  });
}

function send(obj) {
  return new Promise((resolve, reject) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
      log("sent to CLI:", obj.type, obj.requestId || "");
      resolve();
      return;
    }
    // not connected: queue and (re)connect; resolve only once sent
    pending.push({ ...obj, __resolve: resolve });
    log("CLI not connected; queued", obj.type, "(reconnecting…)");
    connect().catch(reject);
  });
}

// ---------------------------------------------------------------- actions
// Make sure content.js is running in a tab. Declarative content_scripts
// only inject on navigation, so tabs opened before the extension was
// (re)loaded have no listener — inject on demand and let the IIFE guard
// (window.__browserstonksLoaded) keep it idempotent.
async function ensureContentScript(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "PING_CONTENT" });
    return; // already there
  } catch {
    // not injected yet — fall through and inject
  }
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["content.js"],
  });
}

// Ask the content script for the current DOM state, ship it to the CLI,
// and let the CLI's reply flow back through ws.onmessage.
async function fillActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return { error: "no active tab" };
  if (!/^https?:/.test(tab.url || "")) {
    return { error: "unsupported page (chrome://, Web Store, etc. cannot be scripted)" };
  }

  await ensureContentScript(tab.id);
  log("content script ready in tab", tab.id);

  const dom = await chrome.tabs.sendMessage(tab.id, { type: "GET_DOM_STATE" })
    .catch((e) => ({ error: "content script not reachable: " + e.message }));
  if (dom?.error) return dom;
  log("DOM snapshot:", dom.forms?.length ?? 0, "form(s);",
      (dom.forms || []).reduce((n, f) => n + (f.fields?.length || 0), 0), "field(s)");

  const requestId = "req_" + (++msgIdSeq.n);
  dom.client = "chrome-extension@0.1.0";
  dom.tabId = tab.id;

  await send({ type: "plan", dom, requestId, sender: "chrome-extension" });
  return { ok: true, requestId, note: "program requested; watch the page" };
}

// popup / keyboard triggers
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "FILL_PAGE") {
    fillActiveTab().then(sendResponse);
    return true; // async response
  }
  if (msg.type === "GET_STATUS") {
    sendResponse({ connected: !!(ws && ws.readyState === WebSocket.OPEN) });
    return;
  }
  // offscreen document can't use chrome.tabs; relay its page calls
  // to the content script in the target tab
  if (msg.type === "PAGE_CALL_RELAY") {
    chrome.tabs.sendMessage(msg.tabId, {
      type: "PAGE_CALL", op: msg.op, args: msg.args || [],
    }).then(sendResponse).catch((e) => sendResponse({ error: e.message }));
    return true; // async response
  }
});

chrome.action.onClicked?.addListener?.(() => {}); // popup handles clicks

// auto-connect at worker startup so ws.onmessage routing is live early
browserstonksGetConfig().then((c) => { if (c.autoConnect) connect(); });
