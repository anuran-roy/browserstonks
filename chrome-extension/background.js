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
      while (pending.length) ws.send(JSON.stringify(pending.shift()));
    };
    ws.onclose = () => {
      log("CLI disconnected; will retry on next action");
      ws = null;
    };
    ws.onerror = () => { /* onclose follows */ };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.requestId && msg.sender && msg.tabId !== undefined) {
        // forward the CLI's plan to the content script in the right tab
        chrome.tabs.sendMessage(msg.tabId, {
          type: "RUN_PROGRAM",
          program: msg.program,
          values: msg.values,
          requestId: msg.requestId,
        }).catch((e) => log("content script unreachable:", e.message));
      }
    };
  });
}

function send(obj) {
  return new Promise((resolve, reject) => {
    const attempt = () => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(obj));
        resolve();
      } else {
        pending.push(obj);
        connect().catch(reject);
        setTimeout(() => { try { resolve(); } catch { /* noop */ } }, 0);
      }
    };
    attempt();
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

  const dom = await chrome.tabs.sendMessage(tab.id, { type: "GET_DOM_STATE" })
    .catch((e) => ({ error: "content script not reachable: " + e.message }));
  if (dom?.error) return dom;

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
});

chrome.action.onClicked?.addListener?.(() => {}); // popup handles clicks

// auto-connect at worker startup so ws.onmessage routing is live early
browserstonksGetConfig().then((c) => { if (c.autoConnect) connect(); });
