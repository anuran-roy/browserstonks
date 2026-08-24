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
let keepaliveTimer = null;

// MV3 service workers are killed after ~30s of inactivity; a websocket
// alone doesn't keep them alive. Ping the CLI every 20s — traffic resets
// the idle timer AND verifies the socket still works.
function startKeepalive() {
  if (keepaliveTimer) return;
  keepaliveTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      try { ws.send(JSON.stringify({ type: "ping" })); } catch { /* onclose follows */ }
    } else {
      // socket dropped (worker was asleep or CLI restarted): reconnect
      connect();
    }
  }, 20000);
}

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

// tell the sidepanel (and any listeners) when the CLI connection changes
function broadcastConnState(connected) {
  chrome.runtime.sendMessage({ type: "CONNECTION_STATE", connected })
    .catch(() => { /* sidepanel closed; ignore */ });
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
      startKeepalive();
      broadcastConnState(true);
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
      reportSession(true); // full snapshot: keep CLI's tracker coherent
    };
    ws.onclose = () => {
      log("CLI disconnected; will retry on next action");
      ws = null;
      broadcastConnState(false);
    };
    ws.onerror = () => { /* onclose follows */ };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      // verbose per-message logging only for non-chatter types
      if (!["pong", "session.ack"].includes(msg.type)) {
        log("message from CLI:", JSON.stringify(msg).slice(0, 300));
      }
      if (msg.error) {
        log("CLI returned an error:", msg.error);
        badge("ERR", "#dc2626");
        return;
      }
      // quiet chatter: pongs + session acks need no handling or logging
      if (msg.type === "pong" || msg.type === "session.ack") {
        return;
      }
      if (msg.type === "browser.events") {
        applyBrowserEvents(msg.events || []);
        return;
      }
      if (msg.type === "tab.read.request") {
        // CLI wants a tab's HTML (harness tab.read / tab.process actions)
        chrome.tabs.sendMessage(msg.tabId, { type: "GET_TAB_HTML" })
          .then((res) => ws.send(JSON.stringify({
            type: "tab.read.response", requestId: msg.requestId,
            html: res?.ok ? res.html : undefined,
            error: res?.ok ? undefined : (res?.error || "content script unreachable"),
          })))
          .catch((e) => ws.send(JSON.stringify({
            type: "tab.read.response", requestId: msg.requestId,
            error: e.message,
          })));
        return;
      }
      if (msg.type === "chat.response" || msg.type === "harnesses.list") {
        deliverChatReply(msg);
        return;
      }
      if (msg.type === "chat.progress") {
        // live model thinking/streaming from the CLI during a long turn
        chrome.runtime.sendMessage({
          type: "CHAT_PROGRESS", requestId: msg.requestId,
          partType: msg.partType, text: msg.text,
        }).catch(() => { /* sidepanel closed; ignore */ });
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
        // only log genuinely unexpected messages, not routing chatter
        if (!["chat.response", "harnesses.list", "chat.progress",
              "tab.read.request", "browser.events"].includes(msg.type)) {
          log("unhandled message from CLI:", msg.type || "(no type)");
        }
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

// ---------------------------------------------------------------- browser events
// Apply CLI-emitted events (tab.new / tab.open / tab.close / doc.open).
async function applyBrowserEvents(events) {
  for (const ev of events) {
    try {
      if (ev.type === "tab.new" || ev.type === "tab.open") {
        const props = { url: ev.url, active: ev.active !== false };
        const tab = ev.windowId != null
          ? await chrome.tabs.create({ ...props, windowId: ev.windowId })
          : await chrome.tabs.create(props);
        log("event", ev.type, "-> tab", tab.id, ev.url);
        reportSession(); // state changed
      } else if (ev.type === "tab.close") {
        await chrome.tabs.remove(ev.tabId);
        log("event tab.close", ev.tabId);
      } else if (ev.type === "doc.open") {
        // docs created by the CLI live on disk; open via file:// URL
        await chrome.tabs.create({ url: ev.url, active: true });
        log("event doc.open", ev.url);
      } else {
        log("ignoring browser event", ev.type);
      }
    } catch (e) {
      log("event", ev.type, "failed:", e.message);
    }
  }
}

// ---------------------------------------------------------------- session reporting
// Keep the CLI's SessionTracker coherent: full snapshot on connect + deltas.
let sessionTimer = null;

async function reportSession(full = false) {
  if (!full && sessionTimer) return;
  if (!full) {
    sessionTimer = setTimeout(() => { sessionTimer = null; reportSessionNow(false); }, 500);
    return;
  }
  await reportSessionNow(true);
}

async function reportSessionNow(full) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  try {
    if (full) {
      const windows = await chrome.windows.getAll({ populate: true });
      await ws.send(JSON.stringify({
        type: "session.hello",
        profile: await getProfileName(),
        windows: windows.map((w) => ({
          id: w.id,
          tabs: (w.tabs || []).map((t) => ({
            id: t.id, url: t.url || "", title: t.title || "", active: t.active,
          })),
        })),
      }));
    } else {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab) {
        await ws.send(JSON.stringify({
          type: "session.tab.updated", windowId: tab.windowId, tabId: tab.id,
          url: tab.url || "", title: tab.title || "", active: true,
        }));
      }
    }
  } catch (e) {
    log("session report failed:", e.message);
  }
}

async function getProfileName() {
  // Chrome profiles aren't exposed to extensions; approximate with a
  // stable per-install identity in storage.
  const { profile } = await chrome.storage.local.get("profile");
  if (profile) return profile;
  const name = "chrome-" + Math.random().toString(36).slice(2, 8);
  await chrome.storage.local.set({ profile: name });
  return name;
}

// tab lifecycle -> session deltas to the CLI
chrome.tabs.onUpdated.addListener((_tabId, changeInfo, tab) => {
  if (changeInfo.url || changeInfo.title || changeInfo.status === "complete") {
    send({
      type: "session.tab.updated", windowId: tab.windowId, tabId: tab.id,
      url: tab.url || "", title: tab.title || "", active: tab.active,
    });
  }
});
chrome.tabs.onRemoved.addListener((tabId, { windowId }) => {
  send({ type: "session.tab.removed", windowId, tabId });
});
chrome.tabs.onActivated.addListener(({ tabId, windowId }) => {
  send({ type: "session.tab.updated", windowId, tabId, active: true });
});
chrome.windows.onRemoved.addListener((windowId) => {
  send({ type: "session.window.removed", windowId });
});

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

// popup / sidepanel triggers
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "FILL_PAGE") {
    fillActiveTab().then(sendResponse);
    return true; // async response
  }
  if (msg.type === "GET_STATUS") {
    sendResponse({ connected: !!(ws && ws.readyState === WebSocket.OPEN) });
    return;
  }
  // sidepanel chat: relay to the CLI with a requestId, then apply any
  // browser events the CLI emitted (tab.new etc.) from the response
  if (msg.type === "CHAT") {
    const requestId = "chat_" + (++msgIdSeq.n);
    send({
      type: "chat", harness: msg.harness, sessionId: msg.sessionId,
      message: msg.message, requestId, sender: "chrome-extension",
    });
    pendingChatReplies.set(requestId, sendResponse);
    return true;
  }
  if (msg.type === "CHAT_STOP") {
    // user pressed Stop: abort the in-flight harness turn on the CLI
    send({
      type: "chat.stop", harness: msg.harness, sessionId: msg.sessionId,
      requestId: "stop_" + (++msgIdSeq.n), sender: "chrome-extension",
    });
    sendResponse({ stopping: true });
    return;
  }
  if (msg.type === "HARNESSES_LIST") {
    send({ type: "harnesses.list", requestId: "hl_" + (++msgIdSeq.n) });
    pendingChatReplies.set("hl_" + msgIdSeq.n, sendResponse);
    return true;
  }
  // offscreen document can't use chrome.tabs; relay its page calls
  // to the content script in the target tab
  if (msg.type === "PAGE_CALL_RELAY") {
    chrome.tabs.sendMessage(msg.tabId, {
      type: "PAGE_CALL", op: msg.op, args: msg.args || [],
    }).then(sendResponse).catch((e) => sendResponse({ error: e.message }));
    return true; // async response
  }
  // read a tab's HTML (for harness tab.read / tab.process actions)
  if (msg.type === "READ_TAB_HTML") {
    chrome.tabs.sendMessage(msg.tabId, { type: "GET_TAB_HTML" })
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: e.message }));
    return true; // async response
  }
});

// chat/harness replies arriving over the websocket: resolve the waiting
// sidepanel callback AND apply any browser events piggybacked on them
const pendingChatReplies = new Map();

function deliverChatReply(msg) {
  const cb = pendingChatReplies.get(msg.requestId);
  if (cb) {
    pendingChatReplies.delete(msg.requestId);
    cb(msg);
  } else {
    log("chat reply with no pending callback:", msg.requestId,
        "(sidepanel reloaded?) — still applying events");
  }
  if (Array.isArray(msg.events) && msg.events.length) {
    log("applying browser events from chat reply:",
        msg.events.map((e) => e.type).join(", "));
    applyBrowserEvents(msg.events).catch((e) =>
      log("applyBrowserEvents failed:", e.message));
  }
}

chrome.sidePanel?.setPanelBehavior({ openPanelOnActionClick: false }).catch?.(() => {});

chrome.action.onClicked?.addListener?.(() => {}); // popup handles clicks

// auto-connect at worker startup so ws.onmessage routing is live early
browserstonksGetConfig().then((c) => { if (c.autoConnect) connect(); });
