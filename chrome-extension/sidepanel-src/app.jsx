// BrowserStonks sidepanel chat app (React + Vercel streamdown).
// Bundled by esbuild into ../sidepanel.js — no CDN, CSP-safe.
import React, { useEffect, useRef, useState, useCallback } from "react";
import { createRoot } from "react-dom/client";
import { Streamdown } from "streamdown";

const HARNESS_ORDER = ["opencode", "openclaw", "hermes"];

// copy button on a message bubble: clipboard write + temporary tick
function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  async function copy(ev) {
    ev.stopPropagation();
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // clipboard API can fail without focus; fall back to a temp textarea
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }
  return (
    <button className="copy-btn" onClick={copy} title="Copy message">
      {copied ? "✓" : "⧉"}
    </button>
  );
}

// one action badge: hover -> rich link preview (title, OG description,
// site name, favicon); click -> focus the exact tab this action opened
function ActionChip({ a, eventTabs }) {
  const [meta, setMeta] = useState(null);
  const [hover, setHover] = useState(false);
  const rawUrl = a.url || a.file || a.tab || "";
  // normalize: bare domains like "marquee-equity.com" become full URLs
  const url = /^https?:\/\//.test(rawUrl) ? rawUrl
    : (/^[\w.-]+\.[a-z]{2,}([\/?#].*)?$/i.test(rawUrl)
        ? "https://" + rawUrl : rawUrl);
  const isWeb = /^https?:\/\//.test(url);

  useEffect(() => {
    if (hover && isWeb && !meta) {
      chrome.runtime.sendMessage({ type: "GET_PAGE_META", url })
        .then(setMeta)
        .catch(() => {});
    }
  }, [hover]); // eslint-disable-line

  async function focusTab() {
    if (!url) return;
    const tabId = eventTabs.get(url);
    const r = await chrome.runtime.sendMessage(
      { type: "FOCUS_TAB", tabId, url: isWeb ? url : undefined })
      .catch(() => null);
    if (!r?.ok) {
      // tab gone (or never opened — failed op): open it fresh
      if (isWeb) chrome.tabs.create({ url, active: true });
    }
  }

  let host = "";
  try { if (isWeb) host = new URL(url).hostname.replace(/^www\./, ""); } catch {}

  return (
    <span className="chip-wrap">
      <button className={"chip " + (a.ok ? "ok" : "bad")} onClick={focusTab}
              onMouseEnter={() => setHover(true)}
              onMouseLeave={() => setHover(false)}
              title={(a.error ? `✕ ${a.error}\n` : "") + (url || a.op)}>
        {a.op}{a.ok === false ? " ✕" : " ✓"}
      </button>
      {hover && isWeb && (
        <span className="chip-preview">
          <span className="preview-head">
            <img className="preview-favicon" src={
              `https://www.google.com/s2/favicons?domain=${encodeURIComponent(url)}&sz=64`}
                 alt="" />
            <span className="preview-host">{host}</span>
          </span>
          <span className="preview-title">
            {meta ? (meta.title || host) : "loading…"}
          </span>
          {meta?.siteName && <span className="preview-site">{meta.siteName}</span>}
          {!a.ok && a.error && <span className="preview-error">✕ {a.error}</span>}
          <span className="preview-desc">
            {meta ? (meta.description || meta.error || "") : ""}
          </span>
          {a.chars ? <span className="preview-site">{a.chars.toLocaleString()} chars read</span> : null}
          {meta?.image && <img className="preview-image" src={meta.image} alt="" />}
          <span className="preview-url">{url}</span>
        </span>
      )}
    </span>
  );
}

function App() {
  const [messages, setMessages] = useState([]); // {role, content, actions?, error?, reasoning?}
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [harnesses, setHarnesses] = useState([]);
  const [harness, setHarness] = useState(
    () => localStorage.getItem("bs:activeHarness") || "opencode");
  const [mode, setMode] = useState(
    () => localStorage.getItem("bs:chatMode") || "plan");
  const [sessionId, setSessionId] = useState(
    () => localStorage.getItem("bs:activeSession") || ("sp-" + Date.now().toString(36)));
  const [savedSessions, setSavedSessions] = useState([]);
  const [connected, setConnected] = useState(false);
  const [progress, setProgress] = useState(null); // {text, reasoning, ts}
  const [stopping, setStopping] = useState(false);
  const eventTabsRef = useRef(new Map()); // url -> real tab id (CLI-opened tabs)
  const [, forceTick] = useState(0);
  const listRef = useRef(null);
  const progressRef = useRef({ text: "", reasoning: "" });

  // persist the active session/harness so reopening the panel resumes it
  useEffect(() => {
    localStorage.setItem("bs:activeSession", sessionId);
  }, [sessionId]);
  useEffect(() => {
    localStorage.setItem("bs:activeHarness", harness);
  }, [harness]);
  useEffect(() => {
    localStorage.setItem("bs:chatMode", mode);
  }, [mode]);

  // on mount: if there's a persisted session with a saved transcript, load it
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;
    const saved = localStorage.getItem("bs:activeSession");
    if (!saved) return;
    (async () => {
      const data = await chrome.runtime.sendMessage(
        { type: "SESSION_LOAD", sessionId: saved }).catch(() => null);
      if (data?.session?.messages?.length) {
        setSessionId(data.session.id);
        if (data.session.harness) setHarness(data.session.harness);
        setMessages(data.session.messages.map((m) => ({
          role: m.role === "assistant" ? "assistant" : m.role,
          content: m.content,
          actions: m.actions || [],
          reasoning: m.reasoning || "",
        })));
      }
    })();
  }, []);

  // live model thinking/streamed text pushed from the background worker
  useEffect(() => {
    const listener = (msg, _sender, sendResponse) => {
      if (msg.type === "CHAT_PROGRESS") {
        const buf = progressRef.current;
        if (msg.partType === "reasoning") buf.reasoning += msg.text;
        else buf.text += msg.text;
        setProgress({ text: buf.text, reasoning: buf.reasoning,
                      ts: Date.now() });
      }
      if (msg.type === "EVENT_TAB") {
        // background opened a real tab for a CLI event — remember the
        // mapping so action badges can jump to the exact tab
        if (msg.url && msg.tabId != null) {
          eventTabsRef.current.set(msg.url, msg.tabId);
          forceTick((n) => n + 1);
        }
      }
      if (msg.type === "CHAT_COMPLETED") {
        // CLI's guaranteed end-of-turn signal (ok/error/timeout/stopped).
        // The reply path usually settles first; this is the safety net
        // when the reply never arrives (hung handler, dropped socket).
        if (msg.status === "timeout") {
          setMessages((m) => [...m, {
            role: "system",
            content: "⚠️ Turn timed out — the harness stopped responding.",
          }]);
        }
        setBusy(false);
        setStopping(false);
        setProgress(null);
      }
      if (msg.type === "CONNECTION_STATE") {
        setConnected(!!msg.connected);
        // system message below the chat when the CLI link breaks
        if (msg.connected === false) {
          setMessages((m) => [...m, {
            role: "system", content: "⚠️ Connection to CLI lost — waiting to reconnect…",
          }]);
        } else if (msg.connected === true) {
          setMessages((m) => [...m, {
            role: "system", content: "✓ Reconnected to CLI",
          }]);
        }
      }
      // ack so the channel doesn't complain
      if (sendResponse) sendResponse({ ok: true });
    };
    chrome.runtime.onMessage.addListener(listener);
    return () => chrome.runtime.onMessage.removeListener(listener);
  }, []);

  // ask the background worker for CLI status + available harnesses + sessions
  const refresh = useCallback(async () => {
    const status = await chrome.runtime.sendMessage({ type: "GET_STATUS" });
    setConnected(!!status?.connected);
    const list = await chrome.runtime.sendMessage({ type: "HARNESSES_LIST" });
    if (list?.harnesses?.length) {
      setHarnesses(list.harnesses);
      if (!list.harnesses.find((h) => h.name === harness)) {
        setHarness(list.harnesses[0].name);
      }
    }
    const sess = await chrome.runtime.sendMessage({ type: "SESSIONS_LIST" })
      .catch(() => null);
    if (sess?.sessions) setSavedSessions(sess.sessions);
  }, [harness]);

  useEffect(() => { refresh(); }, [refresh]);

  // pick a saved session: swap the active session id and restore the transcript
  async function pickSession(value) {
    if (value === "__new__") {
      setSessionId("sp-" + Date.now().toString(36));
      setMessages([]);
      return;
    }
    const data = await chrome.runtime.sendMessage(
      { type: "SESSION_LOAD", sessionId: value }).catch(() => null);
    if (data?.session) {
      setSessionId(data.session.id);
      if (data.session.harness) setHarness(data.session.harness);
      setMessages((data.session.messages || []).map((m) => ({
        role: m.role === "assistant" ? "assistant" : m.role,
        content: m.content,
        actions: m.actions || [],
        reasoning: m.reasoning || "",
      })));
    }
  }

  async function deleteSession(ev, sid) {
    ev.stopPropagation(); ev.preventDefault();
    await chrome.runtime.sendMessage(
      { type: "SESSION_DELETE", sessionId: sid }).catch(() => null);
    // if the deleted session was the persisted active one, start fresh
    if (localStorage.getItem("bs:activeSession") === sid) {
      localStorage.removeItem("bs:activeSession");
      setSessionId("sp-" + Date.now().toString(36));
      setMessages([]);
    }
    refresh();
  }

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages, busy, progress]);

  async function stop() {
    if (!busy) return;
    setStopping(true);
    try {
      await chrome.runtime.sendMessage({
        type: "CHAT_STOP", harness, sessionId,
      });
      // safety: if no response arrives within 10s, unblock the UI anyway
      setTimeout(() => { setStopping(false); }, 10000);
    } catch (e) {
      setStopping(false);
    }
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setBusy(true);
    progressRef.current = { text: "", reasoning: "" };
    setProgress({ text: "", reasoning: "", ts: Date.now() });
    // completion watchdog: the CLI pushes CHAT_COMPLETED exactly once per
    // turn (ok/error/timeout/stopped). If the normal reply callback never
    // fires (hung handler, lost response), this unblocks the UI anyway.
    let settled = false;
    const done = () => {
      if (settled) return false;
      settled = true;
      setBusy(false);
      setStopping(false);
      setProgress(null);
      refresh();
      return true;
    };
    try {
      const resp = await chrome.runtime.sendMessage({ type: "CHAT", harness, sessionId, message: text, mode });
      if (resp?.error) {
        setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${resp.error}`, error: true }]);
      } else {
        setMessages((m) => [...m, {
          role: "assistant",
          content: resp.reply || "*(empty reply)*",
          actions: resp.actionsApplied || [],
          reasoning: resp.meta?.reasoning || "",
        }]);
      }
      done();
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${e.message}`, error: true }]);
      done();
    }
    // final safety net: if neither reply nor completion event arrived
    // within 15 min, unblock anyway
    setTimeout(() => { if (!done()) {
      setMessages((m) => [...m, { role: "system", content: "⚠️ No response from CLI (connection lost?)" }]);
    } }, 15 * 60 * 1000);
  }

  return (
    <div className="app">
      <header>
        <span className={"dot " + (connected ? "on" : "off")} />
        <span className="title">BrowserStonks</span>
        <select value={harness} onChange={(e) => setHarness(e.target.value)} title="Agent harness">
          {(harnesses.length ? harnesses : HARNESS_ORDER.map((n) => ({ name: n, display: n })))
            .filter((h) => h.available !== false)
            .map((h) => (
              <option key={h.name} value={h.name}>{h.display || h.name}</option>
            ))}
        </select>
        <select className="mode-select" value={mode}
                onChange={(e) => setMode(e.target.value)}
                title={mode === "plan"
                  ? "Plan mode: read-only — browse/read/process tabs only"
                  : "Build mode: full — docs, page actions, everything"}>
          <option value="plan">📋 plan</option>
          <option value="build">🔨 build</option>
        </select>
      </header>
      <div className="session-bar">
        <select value={savedSessions.find((s) => s.id === sessionId) ? sessionId : "__new__"}
                onChange={(e) => pickSession(e.target.value)}
                title="Chat session — pick a saved conversation or start new">
          <option value="__new__">＋ New session</option>
          {savedSessions.map((s) => (
            <option key={s.id} value={s.id}>
              {s.harness}: {s.title || s.id} ({s.messages})
            </option>
          ))}
        </select>
        {savedSessions.find((s) => s.id === sessionId) && (
          <button className="session-del" title="Delete this saved session"
                  onClick={(ev) => deleteSession(ev, sessionId)}>🗑</button>
        )}
      </div>

      <div className="messages" ref={listRef}>
        {messages.length === 0 && (
          <div className="empty">
            Chat with your agent harness — it can open tabs, browse, and create docs.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={"bubble " + m.role + (m.error ? " error" : "")}>
            {m.role === "system" && <div className="system-text">{m.content}</div>}
            {m.reasoning && <details className="reasoning">
              <summary>💭 thinking</summary>
              <div className="reasoning-body">{m.reasoning}</div>
            </details>}
            {m.role === "assistant"
              ? <Streamdown>{m.content}</Streamdown>
              : <div className="user-text">{m.content}</div>}
            {m.actions?.length > 0 && (
              <div className="actions">
                {m.actions.map((a, j) => (
                  <ActionChip key={j} a={a} eventTabs={eventTabsRef.current} />
                ))}
              </div>
            )}
            {m.role !== "system" && (
              <div className="msg-footer">
                <CopyButton text={m.content} />
              </div>
            )}
          </div>
        ))}
        {busy && (
          <div className="bubble assistant live">
            {progress?.reasoning && (
              <div className="thinking-stream">
                <div className="thinking-label">💭 thinking…</div>
                <div className="thinking-text">{progress.reasoning.slice(-600)}</div>
              </div>
            )}
            {progress?.text
              ? <Streamdown>{progress.text}</Streamdown>
              : <span className="thinking-label">
                  {progress?.reasoning ? "" : "waiting for the model…"}
                </span>}
            <span className={"pulse" + (progress?.text ? " hidden" : "")} />
          </div>
        )}
      </div>

      <footer>
        <textarea
          value={input}
          placeholder="Ask anything…"
          rows={2}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
          }}
        />
        {busy
          ? <button className="stop-btn" onClick={stop}
                    disabled={stopping}
                    title="Stop the running response">
              {stopping ? "Stopping…" : "■ Stop"}
            </button>
          : <button onClick={send} disabled={!input.trim()}>Send</button>}
      </footer>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
