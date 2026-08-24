// BrowserStonks sidepanel chat app (React + Vercel streamdown).
// Bundled by esbuild into ../sidepanel.js — no CDN, CSP-safe.
import React, { useEffect, useRef, useState, useCallback } from "react";
import { createRoot } from "react-dom/client";
import { Streamdown } from "streamdown";

const HARNESS_ORDER = ["opencode", "openclaw", "hermes"];

function App() {
  const [messages, setMessages] = useState([]); // {role, content, actions?, error?, reasoning?}
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [harnesses, setHarnesses] = useState([]);
  const [harness, setHarness] = useState("opencode");
  const [sessionId] = useState(() => "sp-" + Date.now().toString(36));
  const [connected, setConnected] = useState(false);
  const [progress, setProgress] = useState(null); // {text, reasoning, ts}
  const [stopping, setStopping] = useState(false);
  const listRef = useRef(null);
  const progressRef = useRef({ text: "", reasoning: "" });

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
      const resp = await chrome.runtime.sendMessage({ type: "CHAT", harness, sessionId, message: text });
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
      </header>

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
                  <span key={j} className={"chip " + (a.ok ? "ok" : "bad")}>
                    {a.op}{a.ok === false ? " ✕" : " ✓"}
                  </span>
                ))}
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
