"""Harness plugin system: AI coding agent harnesses for the chat sidepanel.

A "harness" is a source of agentic capability the CLI can drive from chat
(OpenCode, OpenClaw, Hermes, ...). Each harness:

  - opens/attaches to a session (id-scoped, resumable)
  - receives a task (chat message) plus context (browser session snapshot,
    docs, form state) and returns an assistant reply + optional actions
  - is discovered like other plugins: built-in, drop-in plugins/ file, or
    the `browserstonks_harnesses` entry-point group

Harnesses can run local (subprocess/SDK) or remote (HTTP server); the
base class doesn't care — implement start_session/send/stop_session.

Session management: HarnessManager keeps {harness_name: {session_id:
HarnessSession}}; chat requests carry (harness, sessionId) and default
to the manager's "primary" session per harness.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import time
import urllib.request
from typing import Any


class HarnessError(Exception):
    pass


class HarnessSession:
    """One conversation with one harness."""

    def __init__(self, harness: "BaseHarness", session_id: str) -> None:
        self.harness = harness
        self.session_id = session_id
        self.history: list[dict[str, str]] = []  # {role, content}
        self.created_at = time.time()
        self.last_active = time.time()

    def touch(self) -> None:
        self.last_active = time.time()

    def record(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        self.touch()


class BaseHarness:
    """Interface all harness plugins implement."""

    name = "base"
    display_name = "Base"
    default_session = "primary"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    # -- lifecycle -----------------------------------------------------------
    def start_session(self, session_id: str, context: dict[str, Any]) -> HarnessSession:
        return HarnessSession(self, session_id)

    def stop_session(self, session: HarnessSession) -> None:  # pragma: no cover
        pass

    # -- the actual agent turn -------------------------------------------------
    def send(self, session: HarnessSession, message: str,
             context: dict[str, Any] | None = None,
             on_progress=None) -> dict[str, Any]:
        """Return {"reply": str, "actions": [...], "meta": {...}}.

        actions: [{op: "tab.new"|"doc.create"|..., **args}] — applied by
        the CLI's action router, which emits browser events / writes docs.

        on_progress: optional callback({"type": "text"|"reasoning",
        "text": delta}) for live streaming to the chat panel. Harnesses
        that can't stream simply never call it.
        """
        raise NotImplementedError

    # -- shared HTTP helpers (server-mode harnesses) ---------------------------
    def _headers(self) -> dict[str, str]:
        """Auth headers from the harness's `auth` config block.

        auth: {
          "type": "bearer" | "basic" | "apikey" | "none",
          "token": "${OPENCODE_TOKEN}",     # bearer / apikey value
          "username": "...", "password": "...",   # basic
          "header": "X-Api-Key",            # apikey header name
          "query": "api_key",               # apikey query param name
          "headers": {"X-Custom": "v"}      # extra headers, always sent
        }
        """
        auth = self.config.get("auth") or {}
        headers = {"Content-Type": "application/json"}
        for k, v in (auth.get("headers") or {}).items():
            headers[k] = v
        atype = (auth.get("type") or ("bearer" if auth.get("token") else "none")).lower()
        if atype == "bearer" and auth.get("token"):
            headers["Authorization"] = f"Bearer {auth['token']}"
        elif atype == "basic" and auth.get("username"):
            import base64
            cred = f"{auth['username']}:{auth.get('password', '')}"
            headers["Authorization"] = "Basic " + base64.b64encode(cred.encode()).decode()
        elif atype == "apikey" and auth.get("token"):
            headers[auth.get("header", "X-Api-Key")] = auth["token"]
        return headers

    def _auth_query(self) -> str:
        """apikey-as-query-param, appended to URLs when configured."""
        auth = self.config.get("auth") or {}
        if auth.get("query") and auth.get("token"):
            import urllib.parse
            return "?" + urllib.parse.urlencode({auth["query"]: auth["token"]})
        return ""

    def _http(self, method: str, path: str, body: dict | None = None,
              timeout: int = 180) -> Any:
        import time as _time
        base = self.config["base_url"].rstrip("/")
        req = urllib.request.Request(
            f"{base}{path}{self._auth_query()}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers=self._headers(),
        )
        t0 = _time.monotonic()
        _h_log.debug("%s server %s %s", self.name, method, path)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode()
                _h_log.debug("%s server %s %s -> %d bytes (%.0fms)",
                             self.name, method, path, len(raw),
                             (_time.monotonic() - t0) * 1000)
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:  # noqa: BLE001
                pass
            _h_log.error("%s server %s %s -> %d: %s",
                         self.name, method, path, e.code, detail)
            raise HarnessError(
                f"{self.name} server {method} {path} -> {e.code}: {detail}") from e
        except Exception as e:  # noqa: BLE001
            _h_log.error("%s server error (%s): %s", self.name, path, e)
            raise HarnessError(f"{self.name} server error ({path}): {e}") from e


from .prompt import build_prompt as _build_shared_prompt
from .logging_util import log as _log

_h_log = _log("harness")


def _shared_prompt(config: dict, message: str,
                   context: dict[str, Any] | None,
                   mode: str = "plan") -> str:
    """All harnesses share one action-vocabulary system prompt."""
    ctx = context or {}
    return _build_shared_prompt(message, ctx.get("browser", {}),
                                ctx.get("activeTab"), mode=mode,
                                history=ctx.get("_history"),
                                integration=ctx.get("integration",
                                                     "browser:full"))


# ----------------------------------------------------------------------------
# OpenCode (local CLI subprocess or a server speaking its HTTP API)
# ----------------------------------------------------------------------------
class OpenCodeHarness(BaseHarness):
    """OpenCode — local (`opencode` binary) or server (base_url).

    Server mode speaks the real OpenCode HTTP API (verified against
    opencode 1.18.x on :4096):
      POST /session                       -> {id, ...}
      POST /session/{id}/message          -> {info, parts: [{type, text}]}
      body: {parts: [{type: "text", text}], agent?, model?}
    """

    name = "opencode"
    display_name = "OpenCode"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.mode = "server" if self.config.get("base_url") else "local"
        self.sessions: dict[str, str] = {}  # our session id -> opencode session id
        # SSE event bus (server mode): one background thread consuming
        # /event; turns subscribe to events for their session.
        self._sse_events: list[dict] = []
        self._sse_lock = __import__("threading").Lock()
        self._sse_subscribers: dict[str, list] = {}  # sessionID -> [queue]
        self._sse_thread = None

    def _local(self, args: list[str], stdin: str | None = None,
               timeout: int = 120) -> str:
        cmd = [self.config.get("command", "opencode"), *args]
        try:
            _h_log.info("opencode local: %s (stdin %d chars)",
                        " ".join(cmd[:4]), len(stdin or ""))
            proc = subprocess.run(
                cmd, input=stdin, capture_output=True, text=True,
                timeout=timeout, cwd=self.config.get("workdir"),
            )
        except FileNotFoundError as e:
            _h_log.error("opencode binary not found: %s", cmd[0])
            raise HarnessError(
                f"opencode binary not found ({cmd[0]}); install it or set "
                "harnesses.opencode.command") from e
        if proc.returncode != 0:
            _h_log.error("opencode exited %d: %s", proc.returncode,
                         proc.stderr[:300])
            raise HarnessError(f"opencode exited {proc.returncode}: {proc.stderr[:500]}")
        return proc.stdout

    def start_session(self, session_id: str, context: dict[str, Any]) -> HarnessSession:
        sess = super().start_session(session_id, context)
        if self.mode == "server":
            info = self._http("POST", "/session", {})
            self.sessions[session_id] = info.get("id", session_id)
        else:
            self.sessions[session_id] = session_id
        return sess

    def stop_session(self, session: HarnessSession) -> None:
        if self.mode == "server":
            oc_id = self.sessions.get(session.session_id)
            if oc_id:
                try:
                    self._http("POST", f"/session/{oc_id}/abort", {},
                               timeout=15)
                except HarnessError:
                    pass
        self.sessions.pop(session.session_id, None)

    def abort_session(self, oc_session: str) -> None:
        """Abort any in-flight run on the opencode session (user pressed Stop)."""
        try:
            self._http("POST", f"/session/{oc_session}/abort", {}, timeout=15)
            _h_log.info("opencode turn aborted by user (%s)", oc_session)
        except HarnessError as e:
            _h_log.warning("opencode abort failed: %s", e)

    def send(self, session: HarnessSession, message: str,
             context: dict[str, Any] | None = None,
             on_progress=None, stop_event=None) -> dict[str, Any]:
        oc_session = self.sessions.get(session.session_id, session.session_id)
        prompt = _shared_prompt(self.config, message, context, self.config.get("mode", "plan"))
        if self.mode == "server":
            return self._send_server(oc_session, prompt, on_progress,
                                     stop_event)
        out = self._local(
            [oc_session, prompt],
            stdin=None, timeout=self.config.get("timeout", 300))
        return {"reply": out, "actions": _extract_actions(out), "meta": {"mode": "local"}}

    # ------------------------------------------------------------ SSE support
    def _ensure_sse(self) -> bool:
        """Start the /event SSE listener thread (server mode only).

        The same listener auto-replies to tool permission requests when
        `autoApproveTools` is enabled (default: once per tool call).
        """
        if self.mode != "server":
            return False
        if self._sse_thread and self._sse_thread.is_alive():
            return True
        import threading

        def _listen():
            import time as _t
            base = self.config["base_url"].rstrip("/")
            while True:
                try:
                    req = urllib.request.Request(
                        f"{base}/event", headers=self._headers())
                    with urllib.request.urlopen(req, timeout=600) as resp:
                        for raw in resp:
                            line = raw.decode(errors="replace").strip()
                            if not line.startswith("data:"):
                                continue
                            try:
                                ev = json.loads(line[5:].strip())
                            except ValueError:
                                continue
                            self._maybe_auto_approve(ev)
                            self._dispatch_sse(ev)
                except Exception as e:  # noqa: BLE001 - reconnect
                    _h_log.warning("opencode SSE disconnected (%s); "
                                   "reconnecting in 2s", e)
                    _t.sleep(2)

        self._sse_thread = threading.Thread(target=_listen, daemon=True,
                                            name="opencode-sse")
        self._sse_thread.start()
        _h_log.info("opencode SSE event stream connected")
        return True

    def _maybe_auto_approve(self, ev: dict) -> None:
        """Auto-reply to permission requests so unattended turns don't hang.

        Enabled by default (autoApproveTools: true) — reply "once" so each
        tool call runs but nothing is permanently allowed. Set
        autoApproveTools: "always" to allow every future call of a tool,
        or false to disable (turns will hang on permission prompts).
        """
        mode = self.config.get("autoApproveTools", True)
        if not mode:
            return
        etype = ev.get("type", "")
        if etype not in ("permission.asked", "permission.v2.asked"):
            return
        props = ev.get("properties", {}) or {}
        rid = props.get("id")
        if not rid:
            return
        reply = "always" if mode == "always" else "once"
        tool = props.get("permission") or props.get("action") or "?"
        try:
            self._http("POST", f"/permission/{rid}/reply",
                       {"reply": reply}, timeout=15)
            _h_log.info("auto-approved %s tool call (%s)", tool, reply)
        except HarnessError as e:
            _h_log.warning("auto-approve failed for %s: %s", rid, e)

    def _dispatch_sse(self, ev: dict) -> None:
        import queue as _q
        props = ev.get("properties", {}) or {}
        sid = props.get("sessionID") or ""
        etype = ev.get("type", "")
        with self._sse_lock:
            # keep a bounded rolling log for debugging
            self._sse_events.append({"type": etype, "sessionID": sid})
            if len(self._sse_events) > 500:
                del self._sse_events[:250]
            subs = list(self._sse_subscribers.get(sid, []))
        for q in subs:
            try:
                q.put_nowait(ev)
            except _q.Full:
                pass

    def _subscribe(self, session_id: str, maxsize: int = 200):
        import queue as _q
        q = _q.Queue(maxsize=maxsize)
        with self._sse_lock:
            self._sse_subscribers.setdefault(session_id, []).append(q)
        return q

    def _unsubscribe(self, session_id: str, q) -> None:
        with self._sse_lock:
            try:
                self._sse_subscribers.get(session_id, []).remove(q)
            except ValueError:
                pass

    # ------------------------------------------------------------------ env
    def _env_model(self) -> dict[str, str] | None:
        """Model config picked up from .env when the harness config doesn't
        pin one. OPENCODE_MODEL_PROVIDER/OPENCODE_MODEL_ID drive the
        {providerID, modelID} body field on every prompt.

        Also understands the local-provider names (lmstudio/llamacpp/llmd/
        ollama) via their *_MODEL_ID vars — e.g. OLLAMA_MODEL_ID=llama3
        with OLLAMA_MODEL_PROVIDER unset defaults to provider 'ollama'.
        """
        import os
        provider = os.environ.get("OPENCODE_MODEL_PROVIDER", "")
        model_id = os.environ.get("OPENCODE_MODEL_ID", "")
        if not model_id:
            # local fallbacks: OLLAMA_MODEL_ID / LMSTUDIO_MODEL_ID / etc.
            for kind in ("lmstudio", "llamacpp", "llmd", "ollama"):
                mid = os.environ.get(f"{kind.upper()}_MODEL_ID", "")
                if mid:
                    return {"providerID": provider or kind, "modelID": mid}
            return None
        return {"providerID": provider, "modelID": model_id}

    def _send_server(self, oc_session: str, prompt: str, on_progress,
                     stop_event=None) -> dict[str, Any]:
        """Async prompt + live SSE streaming (no polling).

        Falls back to polling when the SSE stream can't be established.
        stop_event breaks the wait loop immediately when the user presses
        Stop; the harness-side run is aborted separately (abort_session).
        """
        import time as _t

        body: dict[str, Any] = {"parts": [{"type": "text", "text": prompt}]}
        agent = self.config.get("agent")
        if agent:
            body["agent"] = agent
        model = self.config.get("model") or self._env_model()
        if model:
            body["model"] = model

        use_sse = self._ensure_sse()
        queue = self._subscribe(oc_session) if use_sse else None
        try:
            # Snapshot messages existing BEFORE our prompt: only NEW
            # assistant messages are this turn's output. Without this,
            # later turns immediately "finish" with the previous reply.
            known_ids = set()
            try:
                for m in self._http(
                        "GET", f"/session/{oc_session}/message", timeout=30):
                    known_ids.add(m.get("info", {}).get("id"))
            except HarnessError:
                pass

            # 204 immediately; the agent works in the background
            self._http("POST", f"/session/{oc_session}/prompt_async", body,
                       timeout=30)

            deadline = _t.monotonic() + self.config.get("turnTimeout", 600)
            sent: dict[str, int] = {}
            assistant_ids: set = set()  # NEW assistant message ids this turn
            user_ids: set = set()       # the new user message(s) — echo guard
            reply = ""
            reasoning = ""

            def _session_busy() -> bool:
                """True while the agent is still running this turn.
                A tool-using turn emits MULTIPLE assistant messages (one
                per tool step); each gets finish=stop but only the last
                carries the text reply. The session-level busy/idle flag
                is the authoritative 'still working' signal."""
                try:
                    st = self._http("GET", "/session/status", timeout=15)
                    return st.get(oc_session, {}).get("type") == "busy"
                except HarnessError:
                    return True  # can't tell — assume still working

            while _t.monotonic() < deadline:
                if stop_event is not None and stop_event.is_set():
                    _h_log.info("opencode wait loop aborted by user stop")
                    return {"reply": "", "actions": [],
                            "meta": {"stopped": True, "session": oc_session}}
                ev = None
                if queue is not None:
                    try:
                        ev = queue.get(timeout=self.config.get("pollInterval", 1.0))
                    except Exception:  # noqa: BLE001 - queue.Empty
                        ev = None
                if ev is None:
                    # SSE idle (or unavailable): fall back to one poll so we
                    # still make progress even if events were missed
                    msgs = self._http("GET", f"/session/{oc_session}/message",
                                      timeout=30)
                    new = self._scan_messages(msgs, known_ids, assistant_ids,
                                              sent, on_progress)
                    reply, reasoning = new if new else (reply, reasoning)
                else:
                    etype = ev.get("type", "")
                    props = ev.get("properties", {}) or {}
                    if etype == "message.part.updated":
                        part = props.get("part", {})
                        # only stream parts of NEW assistant messages —
                        # the user prompt echo (and old messages) must not
                        # appear as model output
                        if (part.get("messageID") not in known_ids
                                and part.get("messageID") not in user_ids):
                            self._emit_part(part, sent, on_progress)
                    elif etype == "message.updated":
                        info = props.get("info", {})
                        mid = info.get("id")
                        if mid in known_ids:
                            continue
                        if info.get("role") == "user":
                            user_ids.add(mid)
                        elif info.get("role") == "assistant":
                            assistant_ids.add(mid)
                            # model/provider errors surface as finish=None
                            # with info.error — fail fast so the user sees
                            # the real cause (e.g. 'No models loaded')
                            if info.get("error"):
                                raise HarnessError(
                                    f"opencode agent error: "
                                    f"{json.dumps(info.get('error'))[:300]}")
                            if info.get("finish") == "error":
                                raise HarnessError(
                                    f"opencode agent error: "
                                    f"{info.get('error', 'unknown')}")
                # a finishing assistant message does NOT mean the turn is
                # done (tool steps finish too) — only the session going
                # idle + a text reply means completion
                if reply and not _session_busy():
                    _h_log.info("opencode turn finished via %s",
                                "SSE" if queue is not None else "polling")
                    return {"reply": reply, "actions": _extract_actions(reply),
                            "meta": {"mode": "server", "session": oc_session,
                                     "reasoning": (reasoning or "")[:2000]}}
            raise HarnessError(
                f"opencode turn did not finish within "
                f"{self.config.get('turnTimeout', 600)}s")
        finally:
            if queue is not None:
                self._unsubscribe(oc_session, queue)

    def _emit_part(self, part: dict, sent: dict, on_progress) -> None:
        """Stream a text/reasoning part delta if it grew."""
        ptype, text = part.get("type"), part.get("text", "") or ""
        pid = part.get("id", id(part))
        if ptype not in ("text", "reasoning") or not text:
            return
        already = sent.get(pid, 0)
        if len(text) > already:
            _h_log.info("opencode streaming %s delta +%d chars (total %d)",
                        ptype, len(text) - already, len(text))
            if on_progress:
                on_progress({"type": ptype, "text": text[already:]})
            sent[pid] = len(text)

    def _scan_messages(self, msgs: list, known_ids: set,
                       assistant_ids: set, sent: dict, on_progress):
        """Scan messages: stream parts of NEW assistant messages, return the
        final reply once one of them has finish=stop."""
        reply, reasoning = "", ""
        for m in msgs:
            info = m.get("info", {})
            mid = info.get("id")
            if mid in known_ids:
                continue  # pre-turn message — not ours
            if info.get("role") != "assistant":
                continue  # user echo and others — never model output
            assistant_ids.add(mid)
            for p in m.get("parts", []):
                self._emit_part(p, sent, on_progress)
            if info.get("error"):
                raise HarnessError(
                    f"opencode agent error: "
                    f"{json.dumps(info.get('error'))[:300]}")
            if info.get("finish") == "error":
                raise HarnessError(
                    f"opencode agent error: {info.get('error', 'unknown')}")
            if info.get("finish") == "stop":
                parts_text = " ".join(
                    p.get("text", "") for p in m.get("parts", [])
                    if p.get("type") == "text")
                if parts_text:
                    reply = parts_text
                r_text = " ".join(
                    p.get("text", "") for p in m.get("parts", [])
                    if p.get("type") == "reasoning")
                if r_text:
                    reasoning = r_text
        return reply, reasoning or None

    def _build_prompt(self, message: str, context: dict[str, Any] | None) -> str:
        # legacy alias: kept so plugins subclassing OpenCodeHarness keep working
        return _shared_prompt(self.config, message, context, self.config.get("mode", "plan"))


# ----------------------------------------------------------------------------
# OpenClaw
# ----------------------------------------------------------------------------
class OpenClawHarness(BaseHarness):
    """OpenClaw gateway — verified against the Gateway HTTP surface on :18789.

    Drives the agent through the always-on tools endpoint:
      POST /tools/invoke {"tool":"sessions_send", args:{sessionKey, message}}
      POST /tools/invoke {"tool":"sessions_history", args:{sessionKey, limit}}
    Auth: gateway token as Bearer (gateway.auth.mode="token").
    """

    name = "openclaw"
    display_name = "OpenClaw"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.mode = "server" if self.config.get("base_url") else "local"
        self.default_session_key = self.config.get(
            "sessionKey", "agent:main:main")

    def _invoke(self, tool: str, args: dict, timeout: int = 180) -> Any:
        data = self._http("POST", "/tools/invoke",
                          {"tool": tool, "args": args}, timeout=timeout)
        if not data.get("ok"):
            err = data.get("error", {})
            raise HarnessError(f"openclaw {tool}: {err.get('message', err)}")
        content = (data.get("result") or {}).get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        if not texts:
            return data.get("result", {}).get("details", {})
        # tool results are JSON-in-text
        try:
            return json.loads(texts[0])
        except (ValueError, TypeError):
            return texts[0]

    def send(self, session: HarnessSession, message: str,
             context: dict[str, Any] | None = None) -> dict[str, Any]:
        prompt = _shared_prompt(self.config, message, context, self.config.get("mode", "plan"))
        if self.mode == "server":
            # one session key per chat session, sent as its own label
            session_key = (self.config.get("sessionKey")
                           or f"{self.default_session_key}")
            send_res = self._invoke("sessions_send", {
                "sessionKey": session_key, "message": prompt})
            # wait for the run to finish, then read the reply from history
            reply = ""
            import time as _t
            deadline = _t.time() + self.config.get("replyTimeout", 120)
            while _t.time() < deadline:
                _t.sleep(self.config.get("pollInterval", 3))
                hist = self._invoke("sessions_history", {
                    "sessionKey": session_key, "limit": 20})
                msgs = hist.get("messages", []) if isinstance(hist, dict) else []
                # last assistant message with content after our prompt
                for m in reversed(msgs):
                    if m.get("role") != "assistant":
                        continue
                    texts = " ".join(
                        c.get("text", "") for c in m.get("content", [])
                        if c.get("type") == "text").strip()
                    if texts:
                        reply = texts
                        break
                    if m.get("stopReason") == "error":
                        raise HarnessError(
                            f"openclaw agent error: {m.get('errorMessage')}")
                if reply:
                    break
            if not reply:
                reply = "(openclaw: no reply within timeout)"
            return {"reply": reply, "actions": _extract_actions(reply),
                    "meta": {"mode": "server", "runId": send_res.get("runId")}}
        # local mode
        cmd = self.config.get("command", "openclaw")
        try:
            proc = subprocess.run(
                [cmd, "chat", "--session", session.session_id],
                input=prompt, capture_output=True, text=True, timeout=120,
                cwd=self.config.get("workdir"),
            )
        except FileNotFoundError as e:
            raise HarnessError(f"{cmd} binary not found") from e
        if proc.returncode != 0:
            raise HarnessError(f"openclaw exited {proc.returncode}: {proc.stderr[:500]}")
        return {"reply": proc.stdout, "actions": _extract_actions(proc.stdout),
                "meta": {"mode": "local"}}


# ----------------------------------------------------------------------------
# Hermes
# ----------------------------------------------------------------------------
class HermesHarness(BaseHarness):
    """Hermes Agent.

    The headless server (:9119) drives the agent over an authenticated
    websocket (needs a ws-ticket login), so the practical server-side path
    is the local CLI: `hermes -z "<prompt>"` runs one agent turn using the
    machine's configured profile. If base_url is set AND reachable we still
    try it first (future-proof for a public chat API), falling back to CLI.
    """

    name = "hermes"
    display_name = "Hermes"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.mode = "server" if self.config.get("base_url") else "local"

    def send(self, session: HarnessSession, message: str,
             context: dict[str, Any] | None = None) -> dict[str, Any]:
        prompt = _shared_prompt(self.config, message, context, self.config.get("mode", "plan"))
        cmd = self.config.get("command", "hermes")
        try:
            proc = subprocess.run(
                [cmd, "-z", prompt],
                capture_output=True, text=True, timeout=180,
            )
        except FileNotFoundError as e:
            raise HarnessError(f"{cmd} binary not found") from e
        if proc.returncode != 0:
            raise HarnessError(f"hermes exited {proc.returncode}: "
                               f"{(proc.stderr or proc.stdout)[:500]}")
        text = proc.stdout.strip()
        return {"reply": text, "actions": _extract_actions(text),
                "meta": {"mode": "cli", "server": self.config.get("base_url")}}


# ----------------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------------
def _extract_text(data: Any) -> str:
    """Best-effort text extraction from various harness response shapes."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("text", "reply", "message", "content", "output", "result"):
            if key in data:
                v = data[key]
                if isinstance(v, str):
                    return v
        # nested message/content shapes
        msg = data.get("message") or data.get("data") or {}
        if isinstance(msg, dict):
            parts = msg.get("parts") or msg.get("content") or []
            if isinstance(parts, list):
                return "\n".join(
                    p.get("text", "") for p in parts if isinstance(p, dict))
    return json.dumps(data)


import re  # noqa: E402  (kept late to keep the harness section readable)

ACTION_RE = re.compile(r"\[\[\s*([a-z.]+)\s*\|([^\]]*)\]\]", re.IGNORECASE)


def _extract_actions(text: str) -> list[dict[str, Any]]:
    """Parse [[op|arg1|arg2...]] action lines out of harness output."""
    actions: list[dict[str, Any]] = []
    for m in ACTION_RE.finditer(text or ""):
        parts = [p.strip() for p in m.group(2).split("|")]
        actions.append({"op": m.group(1).lower(), "args": parts})
    return actions


# ----------------------------------------------------------------------------
# Manager (discovery mirrors the plugin system)
# ----------------------------------------------------------------------------
BUILTIN_HARNESSES: dict[str, type[BaseHarness]] = {
    "opencode": OpenCodeHarness,
    "openclaw": OpenClawHarness,
    "hermes": HermesHarness,
}


class HarnessManager:
    """Owns harness instances + live sessions; plugin-extensible."""

    def __init__(self, config: dict[str, Any] | None = None,
                 extra_harnesses: dict[str, type[BaseHarness]] | None = None) -> None:
        self.config = (config or {}).get("harnesses", {})
        self.registry: dict[str, type[BaseHarness]] = dict(BUILTIN_HARNESSES)
        for name, cls in (extra_harnesses or {}).items():
            self.registry[name] = cls
        self.instances: dict[str, BaseHarness] = {}
        self.sessions: dict[str, dict[str, HarnessSession]] = {}  # harness -> id -> session

    def _harness(self, name: str) -> BaseHarness:
        if name not in self.registry:
            raise HarnessError(
                f"unknown harness '{name}'. available: {sorted(self.registry)}")
        if name not in self.instances:
            self.instances[name] = self.registry[name](self.config.get(name, {}))
        return self.instances[name]

    def chat(self, harness: str, message: str, session_id: str = "primary",
             context: dict[str, Any] | None = None,
             on_progress=None, stop_event=None, mode: str = "plan") -> dict[str, Any]:
        """One agentic turn; returns the harness response dict.

        on_progress (optional) is forwarded to harnesses that stream
        ({"type": "text"|"reasoning", "text": delta} per call).
        stop_event (optional threading.Event) aborts the wait loop promptly
        when the user presses Stop (the harness-side run is aborted by
        ChatHandler.stop separately).
        mode: plan (read-only) | build (docs + page actions).
        """
        h = self._harness(harness)
        live = self.sessions.setdefault(harness, {})
        sess = live.get(session_id)
        if sess is None:
            _h_log.info("starting %s session '%s'", harness, session_id)
            sess = live[session_id] = h.start_session(session_id, context or {})
        sess.record("user", message)
        # mode goes into the harness config so _shared_prompt sees it
        if isinstance(getattr(h, "config", None), dict):
            h.config = {**h.config, "mode": mode}
        try:
            send = h.send
            import inspect as _inspect
            params = _inspect.signature(send).parameters
            kwargs = {}
            if on_progress is not None and "on_progress" in params:
                kwargs["on_progress"] = on_progress
            if stop_event is not None and "stop_event" in params:
                kwargs["stop_event"] = stop_event
            result = send(sess, message, context, **kwargs)
        except HarnessError as e:
            _h_log.error("%s/%s turn failed: %s", harness, session_id, e)
            return {"reply": f"⚠️ harness error: {e}", "actions": [], "meta": {"error": str(e)}}
        sess.record("assistant", result.get("reply", ""))
        result.setdefault("session", {"harness": harness, "id": session_id})
        return result

    def abort(self, harness: str, session_id: str = "primary") -> None:
        """Abort any in-flight run for a harness session (user Stop button)."""
        h = self._harness(harness)
        abort = getattr(h, "abort_session", None)
        live = self.sessions.get(harness, {})
        sess = live.get(session_id)
        if abort is not None and sess is not None:
            # harnesses that map to remote sessions abort them; local-mode
            # harnesses have nothing to abort (subprocesses die on timeout)
            h.stop_session(sess)
            _h_log.info("aborted %s/%s session", harness, session_id)
            # session ended — drop it so the next chat starts fresh
            live.pop(session_id, None)

    def stop_all(self) -> None:
        for name, live in self.sessions.items():
            for sess in live.values():
                try:
                    self._harness(name).stop_session(sess)
                except Exception:  # noqa: BLE001
                    pass
        self.sessions.clear()

    def list_harnesses(self) -> list[dict[str, Any]]:
        return [{"name": k, "display": (v.display_name if hasattr(v, "display_name") else k),
                 "available": bool(self.config.get(k, {}).get("enabled", True))}
                for k, v in self.registry.items()]
