"""Chat handler: routes sidepanel chat to harnesses, applies their actions.

Message flow (sidepanel -> CLI over the websocket integration):

    {"type": "chat", "harness": "opencode", "sessionId": "primary",
     "message": "open https://x.com and take notes"}

The handler:
  1. builds context (browser session snapshot, active tab, doc dir)
  2. runs one agentic turn on the requested harness (HarnessManager.chat)
  3. applies the harness's actions:
       tab.new / tab.open -> EventBus (browser applies; recorded if headless)
       tab.close          -> EventBus
       doc.create         -> writes the doc file, EventBus.doc_open
  4. returns a chat response for the sidepanel to render

Doc creation is the "CLI creating docs (HTML, markdown, etc)" path: the
harness streams the document as a fenced block after the action line, or
inline content in a third arg.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from .harnesses import HarnessError, HarnessManager
from .logging_util import log as _log
from .session import EventBus, SessionTracker

_chat_log = _log("chat")
_act_log = _log("actions")


async def _maybe_await(value: Any) -> Any:
    import inspect
    if inspect.isawaitable(value):
        return await value
    return value


def _run_process_code(code: str, html: str, timeout: float = 30) -> Any:
    """Execute harness-written process(HTML) in a restricted namespace.

    Trust model: this code comes from the agent harness the operator
    configured — same trust level as the fill programs it already
    generates. Runs in-process with stdlib available (html.parser, re,
    json, ...), no network, result must be JSON-serializable.
    """
    import signal

    ns: dict[str, Any] = {}
    exec(compile(code, "<harness-process>", "exec"), ns)  # noqa: S102
    process = ns.get("process")
    if not callable(process):
        raise ValueError("code does not define process(HTML)")

    def _timeout(signum, frame):  # pragma: no cover
        raise TimeoutError("process(HTML) exceeded time limit")

    old = signal.signal(signal.SIGALRM, _timeout)  # type: ignore[attr-defined]
    try:
        signal.setitimer(signal.ITIMER_REAL, timeout)  # type: ignore[attr-defined]
        outcome = process(html)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)  # type: ignore[attr-defined]
        signal.signal(signal.SIGALRM, old)  # type: ignore[attr-defined]
    # force JSON-serializability early so failures surface here
    json.dumps(outcome)
    return outcome


class ChatHandler:
    def __init__(self, harness_manager: HarnessManager,
                 tracker: SessionTracker, bus: EventBus,
                 docs_dir: Path | None = None,
                 tab_reader: Any = None,
                 max_rounds: int = 256) -> None:
        self.harnesses = harness_manager
        self.tracker = tracker
        self.bus = bus
        self.docs_dir = docs_dir or Path.cwd() / "docs"
        # callable(tab_id) -> html str (sync or async); None when the
        # active integration can't read tabs (e.g. stdio-only mode).
        # Set here OR injected later by serve() for integrations that
        # only become readable once started.
        self.tab_reader = tab_reader
        # max read/process rounds per chat turn (long research tasks
        # need many; the harness stops the loop when it stops asking)
        self.max_rounds = max_rounds
        # stop flag for in-flight turns (user pressed Stop in the panel)
        import threading as _th
        self._stop_events: dict[tuple, "_th.Event"] = {}
        # persistent chat history (config root / sessions/)
        from .session_store import SessionStore
        self.store = SessionStore(docs_dir.parent / "sessions")

    # ----------------------------------------------------------- session store
    def sessions_list(self) -> list[dict]:
        return self.store.list()

    def session_load(self, sid: str) -> dict:
        data = self.store.load(sid)
        if data is None:
            return {"error": f"no saved session '{sid}'"}
        return {"type": "session.load", "session": data}

    def session_delete(self, sid: str) -> dict:
        ok = self.store.delete(sid)
        return {"type": "session.delete", "deleted": ok}

    def _record_turn(self, harness: str, session_id: str,
                     user_msg: str, reply: str, actions: list,
                     reasoning: str = "") -> None:
        """Persist one user+assistant exchange to the session store."""
        try:
            data = self.store.load(session_id) or {
                "id": session_id, "messages": [], "created": time.time()}
            msgs = data.get("messages", [])
            msgs.append({"role": "user", "content": user_msg,
                         "ts": time.time()})
            msgs.append({"role": "assistant", "content": reply,
                         "ts": time.time(), "actions": actions or [],
                         "reasoning": reasoning or ""})
            self.store.save(session_id, harness, msgs)
        except Exception as e:  # noqa: BLE001 - persistence must not kill turns
            _chat_log.warning("session save failed: %s", e)

    # ------------------------------------------------------------------ turn
    async def handle(self, msg: dict[str, Any], emit=None) -> dict[str, Any]:
        """emit: optional async callable(dict) receiving live progress events
        ({type: "chat.progress", requestId, partType, text}) so the chat
        panel can show the model thinking while it works."""
        harness = msg.get("harness", "opencode")
        session_id = msg.get("sessionId", "primary")
        import threading as _threading
        stop_event = _threading.Event()
        self._stop_events[(harness, session_id)] = stop_event
        try:
            return await self._handle_inner(msg, emit, stop_event)
        finally:
            self._stop_events.pop((harness, session_id), None)

    def stop(self, harness: str, session_id: str) -> bool:
        """Stop an in-flight turn (user pressed Stop). Returns True if a
        turn was running and got signalled."""
        ev = self._stop_events.get((harness, session_id))
        if ev is None:
            return False
        ev.set()
        # also abort the harness-side run (e.g. OpenCode session abort)
        try:
            self.harnesses.abort(harness, session_id)
        except Exception as e:  # noqa: BLE001
            _chat_log.warning("harness abort failed: %s", e)
        _chat_log.info("stop signalled for %s/%s", harness, session_id)
        return True

    async def _handle_inner(self, msg: dict[str, Any], emit,
                            stop_event) -> dict[str, Any]:
        harness = msg.get("harness", "opencode")
        session_id = msg.get("sessionId", "primary")
        message = (msg.get("message") or "").strip()
        if not message:
            return {"type": "chat.response", "error": "empty message"}

        request_id = msg.get("requestId")
        # capture the event loop here (we're ON it) so the sync on_progress
        # callback — invoked from harness SSE/subprocess threads — can
        # schedule emits back onto this loop.
        import asyncio as _aio
        _loop = _aio.get_running_loop()

        def on_progress(delta: dict) -> None:
            """Sync callback from harness threads; schedule the async emit."""
            if emit is None:
                return
            try:
                ev = {"type": "chat.progress", "requestId": request_id,
                      "harness": harness,
                      "partType": delta.get("type", "text"),
                      "text": delta.get("text", "")}
                _aio.run_coroutine_threadsafe(_maybe_await(emit(ev)), _loop)
            except RuntimeError:
                pass  # loop closed — skip progress
            except Exception:  # noqa: BLE001 - never let progress kill a turn
                pass

        context = self._context()
        _chat_log.info("turn start %s/%s (message %d chars)",
                       harness, session_id, len(message))
        reply_text = message
        applied_total: list[dict[str, Any]] = []
        result: dict[str, Any] = {}
        # multi-step work loop: keep executing read/process rounds until the
        # harness stops requesting them (or the round limit is hit).
        # Long research tasks legitimately need many rounds.
        max_rounds = self.max_rounds
        for _round in range(max_rounds):
            if stop_event.is_set():
                _chat_log.info("turn stopped by user before round %d",
                               _round + 1)
                return {"type": "chat.response", "harness": harness,
                        "reply": "⏹ Stopped by user.",
                        "actionsApplied": applied_total,
                        "events": self.bus.drain(), "meta": {"stopped": True}}
            _chat_log.debug("harness round %d: sending %d chars",
                            _round + 1, len(reply_text))
            # run the (blocking) harness turn in a thread so the event loop
            # stays free to deliver chat.progress emits and chat.stop
            import asyncio as _aio
            result = await _aio.get_running_loop().run_in_executor(
                None,
                lambda: self.harnesses.chat(
                    harness, reply_text, session_id, context,
                    on_progress=on_progress, stop_event=stop_event))
            reply_text = ""
            if stop_event.is_set():
                _chat_log.info("turn stopped by user mid-round %d", _round + 1)
                return {"type": "chat.response", "harness": harness,
                        "reply": "⏹ Stopped by user.",
                        "actionsApplied": applied_total,
                        "events": self.bus.drain(), "meta": {"stopped": True}}
            actions = result.get("actions", [])
            if not any(a["op"] in ("tab.read", "tab.process") for a in actions):
                break
            _chat_log.info("round %d: harness requested %s -> executing",
                           _round + 1,
                           [a["op"] for a in actions
                            if a["op"] in ("tab.read", "tab.process")])
            # execute actions; read/process results (success AND failure)
            # go back to the harness so it can adapt (e.g. open the tab
            # first, then retry the read)
            results_payload = []
            # if this batch opens tabs, give the browser a moment to
            # actually open + report them before reads resolve
            if any(a["op"] in ("tab.new", "tab.open") for a in actions):
                await _aio.sleep(2.0)
            applied = await self._apply_actions(actions, message, result,
                                                results_out=results_payload)
            applied_total.extend(applied)
            # flush browser events (tab.new etc.) to the browser NOW so
            # they take effect before the harness's next round — otherwise
            # tab.new + tab.read in one reply always race
            if emit is not None and self.bus.events:
                await _maybe_await(emit({"type": "browser.events",
                                         "events": self.bus.drain()}))
            if not results_payload:
                _chat_log.warning("round %d: no read/process results "
                                  "(all failed?) — stopping loop", _round + 1)
                # prevent the final apply from re-applying these actions
                result["actions"] = []
                break
            reply_text = ("ACTION RESULTS (from your tab.read/tab.process "
                          "actions; failures included — adapt and retry if "
                          "needed):\n"
                          + json.dumps(results_payload, indent=2)[:60000])
        else:
            _chat_log.warning("read/process loop hit round limit")
            result = {"reply": "⚠️ too many read/process rounds", "actions": []}

        applied = await self._apply_actions(result.get("actions", []), message, result)
        applied_total.extend(applied)
        _record = {
            "type": "chat.response", "harness": harness,
            "session": {"harness": harness, "id": session_id},
            "reply": result.get("reply", ""),
            "actionsApplied": applied_total,
            "events": self.bus.drain(),
            "meta": result.get("meta", {}),
        }
        # persist the exchange (never fatal)
        self._record_turn(harness, session_id, message,
                          result.get("reply", ""), applied_total,
                          (result.get("meta") or {}).get("reasoning", ""))
        _chat_log.info("turn complete %s/%s: reply %d chars, %d action(s)",
                       harness, session_id, len(result.get("reply", "")),
                       len(applied_total))
        return _record

    def _context(self) -> dict[str, Any]:
        active = self.tracker.active_tab()
        return {
            "browser": self.tracker.snapshot(),
            "activeTab": active.to_dict() if active else None,
            "docsDir": str(self.docs_dir),
        }

    # -------------------------------------------------------------- actions
    async def _apply_actions(self, actions: list[dict[str, Any]],
                       original_message: str = "",
                       result: dict[str, Any] | None = None,
                       results_out: list | None = None) -> list[dict[str, Any]]:
        applied: list[dict[str, Any]] = []
        for act in actions:
            op = act.get("op", "")
            args = act.get("args", [])
            try:
                if op in ("tab.new", "tab.open"):
                    url = args[0] if args else ""
                    ev = self.bus.tab_new(url)
                    _act_log.info("tab.new %s -> event queued", url)
                    applied.append({"op": op, "url": url, "ok": True,
                                    "event": ev["type"]})
                elif op == "tab.close":
                    tab_id = int(args[0]) if args and args[0].isdigit() else None
                    if tab_id is None:
                        active = self.tracker.active_tab()
                        tab_id = active.tab_id if active else None
                    if tab_id is not None:
                        ev = self.bus.tab_close(tab_id)
                        _act_log.info("tab.close %s -> event queued", tab_id)
                        applied.append({"op": op, "tabId": tab_id, "ok": True})
                    else:
                        _act_log.warning("tab.close: no tab id and no active tab")
                        applied.append({"op": op, "ok": False,
                                        "error": "no tab id and no active tab"})
                elif op == "tab.read":
                    entry = await self._tab_read(args)
                    applied.append({"op": op, "ok": entry.get("ok", False),
                                    "error": entry.get("error")})
                    if results_out is not None:
                        # success -> HTML; failure -> error the harness can
                        # act on (open the tab first, then retry)
                        results_out.append({
                            "action": "tab.read",
                            "tab": entry.get("tab", args[0] if args else "?"),
                            "html": entry.get("html"),
                            "error": entry.get("error"),
                        })
                elif op == "tab.process":
                    entry = await self._tab_process(args, result)
                    applied.append({"op": op, "ok": entry.get("ok", False),
                                    "error": entry.get("error")})
                    if results_out is not None:
                        results_out.append({
                            "action": "tab.process",
                            "tab": entry.get("tab", args[0] if args else "?"),
                            "result": entry.get("result"),
                            "error": entry.get("error"),
                        })
                elif op == "doc.create":
                    fmt = args[0] if len(args) > 0 else "markdown"
                    filename = args[1] if len(args) > 1 else "doc.md"
                    content = self._doc_content(fmt, filename, result, original_message)
                    path = self._write_doc(filename, content)
                    ev = self.bus.doc_open(
                        f"file://{path.resolve()}")
                    _act_log.info("doc.create %s (%s, %d chars) -> %s",
                                  filename, fmt, len(content), path)
                    applied.append({"op": "doc.create", "file": str(path.resolve()),
                                    "format": fmt, "ok": True, "chars": len(content)})
                else:
                    _act_log.warning("unknown action op '%s' (args=%s)", op, args)
                    applied.append({"op": op, "ok": False,
                                    "error": f"unknown action op '{op}'"})
            except Exception as e:  # noqa: BLE001
                _act_log.warning("action %s failed: %s", op, e)
                applied.append({"op": op, "ok": False, "error": str(e)})
        return applied

    # ------------------------------------------------------------ tab read/process
    def _resolve_tab(self, arg: str) -> dict[str, Any]:
        """Resolve 'tabIdOrUrl' to a tracked tab (id + url)."""
        if not arg:
            active = self.tracker.active_tab()
            if active:
                return {"tab_id": active.tab_id, "url": active.url}
            return {"error": "no argument and no active tab"}
        if arg.isdigit():
            tab = self.tracker.find_tab(tab_id=int(arg))
            if tab:
                return {"tab_id": tab.tab_id, "url": tab.url}
            return {"error": f"tab {arg} not found in session state"}
        tab = self.tracker.find_tab(url_contains=arg)
        if tab:
            return {"tab_id": tab.tab_id, "url": tab.url}
        return {"error": f"no open tab matches '{arg}'"}

    async def _tab_read(self, args: list[str]) -> dict[str, Any]:
        """Fetch a tab's HTML via the integration (extension reads the DOM)."""
        target = self._resolve_tab(args[0] if args else "")
        if "error" in target:
            _act_log.warning("tab.read resolve failed: %s", target["error"])
            return target
        reader = getattr(self, "tab_reader", None)
        if reader is None:
            _act_log.warning("tab.read: no tab_reader wired (integration "
                             "cannot read tabs)")
            return {"error": "this integration cannot read tabs"}
        try:
            _act_log.info("tab.read tab %s (%s)…", target["tab_id"], target["url"])
            html = await _maybe_await(reader(target["tab_id"]))
        except Exception as e:  # noqa: BLE001
            _act_log.warning("tab.read failed for tab %s: %s", target["tab_id"], e)
            return {"error": f"tab read failed: {e}", "tab": target["url"]}
        # keep payloads sane for the harness context window
        html = (html or "")[:100_000]
        _act_log.info("tab.read ok: %d chars from %s", len(html), target["url"])
        return {"ok": True, "tab": target["url"], "html": html}

    async def _tab_process(self, args: list[str],
                           result: dict[str, Any] | None) -> dict[str, Any]:
        """Run harness-written process(HTML) code against a tab's HTML."""
        target = self._resolve_tab(args[0] if args else "")
        if "error" in target:
            _act_log.warning("tab.process resolve failed: %s", target["error"])
            return target
        code = self._fenced_code(result or {}, "python")
        if not code or "def process" not in code:
            _act_log.warning("tab.process: missing ```python process(HTML) block")
            return {"error": "tab.process requires a fenced ```python block "
                             "defining process(HTML)"}
        reader = getattr(self, "tab_reader", None)
        if reader is None:
            return {"error": "this integration cannot read tabs"}
        try:
            _act_log.info("tab.process tab %s (%s), %d chars of code…",
                          target["tab_id"], target["url"], len(code))
            html = (await _maybe_await(reader(target["tab_id"])))[:200_000]
            outcome = _run_process_code(code, html)
        except Exception as e:  # noqa: BLE001
            _act_log.warning("tab.process failed for tab %s: %s", target["tab_id"], e)
            return {"error": f"tab process failed: {e}", "tab": target["url"]}
        _act_log.info("tab.process ok: %s", json.dumps(outcome)[:200])
        return {"ok": True, "tab": target["url"], "result": outcome}

    @staticmethod
    def _fenced_code(result: dict, lang: str) -> str:
        m = re.search(rf"```{lang}\s*\n(.*?)```",
                      result.get("reply", ""), re.DOTALL)
        return m.group(1).strip() if m else ""

    # ------------------------------------------------------------------ docs
    def _doc_content(self, fmt: str, filename: str,
                     result: dict[str, Any] | None,
                     original_message: str) -> str:
        """Find the document body: explicit arg > fenced block > the reply."""
        reply = (result or {}).get("reply", "")
        # fenced block matching the format (```html / ```markdown / ```md)
        fence = re.search(
            r"```(?:html|markdown|md|txt)?\s*\n(.*?)```", reply, re.DOTALL)
        if fence:
            return fence.group(1).strip()
        return reply.strip() or f"# Document\n\n{original_message}\n"

    def _write_doc(self, filename: str, content: str) -> Path:
        # sanitize: no traversal, no leading dots
        filename = Path(filename).name or "doc.md"
        ext = {"html": ".html", "markdown": ".md", "md": ".md",
               "text": ".txt", "txt": ".txt"}
        fmt_ext = Path(filename).suffix.lower()
        if fmt_ext not in ext.values():
            filename += ext.get("markdown", ".md")
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        path = self.docs_dir / filename
        path.write_text(content, encoding="utf-8")
        return path
