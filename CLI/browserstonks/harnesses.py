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
             context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return {"reply": str, "actions": [...], "meta": {...}}.

        actions: [{op: "tab.new"|"doc.create"|..., **args}] — applied by
        the CLI's action router, which emits browser events / writes docs.
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
        base = self.config["base_url"].rstrip("/")
        req = urllib.request.Request(
            f"{base}{path}{self._auth_query()}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:  # noqa: BLE001
                pass
            raise HarnessError(
                f"{self.name} server {method} {path} -> {e.code}: {detail}") from e
        except Exception as e:  # noqa: BLE001
            raise HarnessError(f"{self.name} server error ({path}): {e}") from e


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

    def _local(self, args: list[str], stdin: str | None = None,
               timeout: int = 120) -> str:
        cmd = [self.config.get("command", "opencode"), *args]
        try:
            proc = subprocess.run(
                cmd, input=stdin, capture_output=True, text=True,
                timeout=timeout, cwd=self.config.get("workdir"),
            )
        except FileNotFoundError as e:
            raise HarnessError(
                f"opencode binary not found ({cmd[0]}); install it or set "
                "harnesses.opencode.command") from e
        if proc.returncode != 0:
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
                    self._http("DELETE", f"/session/{oc_id}")
                except HarnessError:
                    pass
        self.sessions.pop(session.session_id, None)

    def send(self, session: HarnessSession, message: str,
             context: dict[str, Any] | None = None) -> dict[str, Any]:
        oc_session = self.sessions.get(session.session_id, session.session_id)
        prompt = self._build_prompt(message, context)
        if self.mode == "server":
            body: dict[str, Any] = {
                "parts": [{"type": "text", "text": prompt}],
            }
            agent = self.config.get("agent")
            if agent:
                body["agent"] = agent
            model = self.config.get("model")
            if model:
                body["model"] = model
            data = self._http("POST", f"/session/{oc_session}/message", body)
            text = "\n".join(
                p.get("text", "") for p in data.get("parts", [])
                if p.get("type") == "text")
            return {"reply": text, "actions": _extract_actions(text),
                    "meta": {"mode": "server",
                             "session": data.get("info", {}).get("id", oc_session)}}
        out = self._local(["run", "--session", oc_session, "--format", "text"], stdin=prompt)
        return {"reply": out, "actions": _extract_actions(out), "meta": {"mode": "local"}}

    def _build_prompt(self, message: str, context: dict[str, Any] | None) -> str:
        ctx = context or {}
        parts = [
            "You are driving a browser automation assistant (BrowserStonks).",
            "You may reply with normal markdown, and you may include action",
            "lines on their own rows, in exactly this format:",
            "    [[tab.new|https://example.com]]",
            "    [[doc.create|markdown|<filename>.md]]  (second arg: html|markdown|text)",
            "Browser session state follows as JSON:",
            json.dumps(ctx.get("browser", {}), indent=2)[:4000],
            f"User: {message}",
        ]
        return "\n".join(parts)


# ----------------------------------------------------------------------------
# OpenClaw
# ----------------------------------------------------------------------------
class OpenClawHarness(BaseHarness):
    """OpenClaw agent harness (local binary or HTTP endpoint)."""

    name = "openclaw"
    display_name = "OpenClaw"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.mode = "server" if self.config.get("base_url") else "local"

    def send(self, session: HarnessSession, message: str,
             context: dict[str, Any] | None = None) -> dict[str, Any]:
        prompt = OpenCodeHarness(self.config)._build_prompt(message, context)
        if self.mode == "server":
            data = self._http("POST", "/chat",
                              {"message": prompt, "session": session.session_id})
            text = _extract_text(data)
        else:
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
            text = proc.stdout
        return {"reply": text, "actions": _extract_actions(text), "meta": {"mode": self.mode}}


# ----------------------------------------------------------------------------
# Hermes
# ----------------------------------------------------------------------------
class HermesHarness(BaseHarness):
    """Hermes Agent — via its CLI (`hermes` binary) or an HTTP gateway."""

    name = "hermes"
    display_name = "Hermes"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.mode = "server" if self.config.get("base_url") else "local"

    def send(self, session: HarnessSession, message: str,
             context: dict[str, Any] | None = None) -> dict[str, Any]:
        prompt = OpenCodeHarness(self.config)._build_prompt(message, context)
        if self.mode == "server":
            data = self._http("POST", "/v1/chat", {
                "message": prompt, "session": session.session_id})
            text = _extract_text(data)
        else:
            cmd = self.config.get("command", "hermes")
            try:
                proc = subprocess.run(
                    [cmd, "-p", prompt],
                    capture_output=True, text=True, timeout=180,
                )
            except FileNotFoundError as e:
                raise HarnessError(f"{cmd} binary not found") from e
            if proc.returncode != 0:
                raise HarnessError(f"hermes exited {proc.returncode}: {proc.stderr[:500]}")
            text = proc.stdout
        return {"reply": text, "actions": _extract_actions(text), "meta": {"mode": self.mode}}


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
             context: dict[str, Any] | None = None) -> dict[str, Any]:
        """One agentic turn; returns the harness response dict."""
        h = self._harness(harness)
        live = self.sessions.setdefault(harness, {})
        sess = live.get(session_id)
        if sess is None:
            sess = live[session_id] = h.start_session(session_id, context or {})
        sess.record("user", message)
        try:
            result = h.send(sess, message, context)
        except HarnessError as e:
            return {"reply": f"⚠️ harness error: {e}", "actions": [], "meta": {"error": str(e)}}
        sess.record("assistant", result.get("reply", ""))
        result.setdefault("session", {"harness": harness, "id": session_id})
        return result

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
