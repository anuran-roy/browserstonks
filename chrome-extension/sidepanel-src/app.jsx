// BrowserStonks sidepanel chat app (React + Vercel streamdown).
// Bundled by esbuild into ../sidepanel.js — no CDN, CSP-safe.
import React, { useEffect, useRef, useState, useCallback } from "react";
import { createRoot } from "react-dom/client";
import { Streamdown } from "streamdown";

const HARNESS_ORDER = ["opencode", "openclaw", "hermes"];

function App() {
  const [messages, setMessages] = useState([]); // {role, content, actions?, error?}
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [harnesses, setHarnesses] = useState([]);
  const [harness, setHarness] = useState("opencode");
  const [sessionId] = useState(() => "sp-" + Date.now().toString(36));
  const [connected, setConnected] = useState(false);
  const listRef = useRef(null);

  // ask the background worker for CLI status + available harnesses
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
  }, [harness]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages, busy]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setBusy(true);
    try {
      const resp = await chrome.runtime.sendMessage({
        type: "CHAT", harness, sessionId, message: text,
      });
      if (resp?.error) {
        setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${resp.error}`, error: true }]);
      } else {
        setMessages((m) => [...m, {
          role: "assistant",
          content: resp.reply || "*(empty reply)*",
          actions: resp.actionsApplied || [],
        }]);
      }
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${e.message}`, error: true }]);
    } finally {
      setBusy(false);
      refresh();
    }
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
      </header>

      <div className="messages" ref={listRef}>
        {messages.length === 0 && (
          <div className="empty">
            Chat with your agent harness — it can open tabs, browse, and create docs.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={"bubble " + m.role + (m.error ? " error" : "")}>
            {m.role === "assistant"
              ? <Streamdown>{m.content}</Streamdown>
              : <div className="user-text">{m.content}</div>}
            {m.actions?.length > 0 && (
              <div className="actions">
                {m.actions.map((a, j) => (
                  <span key={j} className={"chip " + (a.ok ? "ok" : "bad")}>
                    {a.op}{a.ok === false ? " ✕" : " ✓"}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="bubble assistant thinking">thinking…</div>}
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
        <button onClick={send} disabled={busy || !input.trim()}>Send</button>
      </footer>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
