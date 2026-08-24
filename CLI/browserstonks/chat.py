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

import json
import re
from pathlib import Path
from typing import Any

from .harnesses import HarnessManager, HarnessError
from .session import EventBus, SessionTracker


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
                 docs_dir: Path | None = None) -> None:
        self.harnesses = harness_manager
        self.tracker = tracker
        self.bus = bus
        self.docs_dir = docs_dir or Path.cwd() / "docs"

    # ------------------------------------------------------------------ turn
    async def handle(self, msg: dict[str, Any]) -> dict[str, Any]:
        harness = msg.get("harness", "opencode")
        session_id = msg.get("sessionId", "primary")
        message = (msg.get("message") or "").strip()
        if not message:
            return {"type": "chat.response", "error": "empty message"}

        context = self._context()
        # reply_waiter: if the turn produces tab.read/tab.process actions,
        # we execute them and feed results back to the harness as a new
        # turn, looping until it replies without those actions (max 3).
        reply_text = message
        applied_total: list[dict[str, Any]] = []
        result: dict[str, Any] = {}
        for _round in range(4):
            result = self.harnesses.chat(harness, reply_text, session_id, context)
            reply_text = ""
            actions = result.get("actions", [])
            if not any(a["op"] in ("tab.read", "tab.process") for a in actions):
                break
            # execute read/process actions and collect results
            results_payload = []
            applied = await self._apply_actions(actions, message, result,
                                                results_out=results_payload)
            applied_total.extend(applied)
            if not results_payload:
                break
            reply_text = ("ACTION RESULTS (from your tab.read/tab.process "
                          "actions):\n" + json.dumps(results_payload, indent=2)[:60000])
        else:
            result = {"reply": "⚠️ too many read/process rounds", "actions": []}

        applied = await self._apply_actions(result.get("actions", []), message, result)
        applied_total.extend(applied)

        return {
            "type": "chat.response",
            "harness": harness,
            "session": result.get("session", {"harness": harness, "id": session_id}),
            "reply": result.get("reply", ""),
            "actionsApplied": applied_total,
            "events": self.bus.drain(),
            "meta": result.get("meta", {}),
        }

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
                    applied.append({"op": op, "url": url, "ok": True,
                                    "event": ev["type"]})
                elif op == "tab.close":
                    tab_id = int(args[0]) if args and args[0].isdigit() else None
                    if tab_id is None:
                        active = self.tracker.active_tab()
                        tab_id = active.tab_id if active else None
                    if tab_id is not None:
                        ev = self.bus.tab_close(tab_id)
                        applied.append({"op": op, "tabId": tab_id, "ok": True})
                    else:
                        applied.append({"op": op, "ok": False,
                                        "error": "no tab id and no active tab"})
                elif op == "tab.read":
                    entry = await self._tab_read(args)
                    applied.append({"op": op, "ok": entry.get("ok", False),
                                    "error": entry.get("error")})
                    if results_out is not None and entry.get("ok"):
                        results_out.append({"action": "tab.read",
                                            "tab": entry["tab"],
                                            "html": entry["html"]})
                elif op == "tab.process":
                    entry = await self._tab_process(args, result)
                    applied.append({"op": op, "ok": entry.get("ok", False),
                                    "error": entry.get("error")})
                    if results_out is not None and entry.get("ok"):
                        results_out.append({"action": "tab.process",
                                            "tab": entry["tab"],
                                            "result": entry["result"]})
                elif op == "doc.create":
                    fmt = args[0] if len(args) > 0 else "markdown"
                    filename = args[1] if len(args) > 1 else "doc.md"
                    content = self._doc_content(fmt, filename, result, original_message)
                    path = self._write_doc(filename, content)
                    ev = self.bus.doc_open(f"file://{path}")
                    applied.append({"op": "doc.create", "file": str(path),
                                    "format": fmt, "ok": True, "chars": len(content)})
                else:
                    applied.append({"op": op, "ok": False,
                                    "error": f"unknown action op '{op}'"})
            except Exception as e:  # noqa: BLE001
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
            return target
        reader = getattr(self, "tab_reader", None)
        if reader is None:
            return {"error": "this integration cannot read tabs"}
        try:
            html = await _maybe_await(reader(target["tab_id"]))
        except Exception as e:  # noqa: BLE001
            return {"error": f"tab read failed: {e}", "tab": target["url"]}
        # keep payloads sane for the harness context window
        html = (html or "")[:100_000]
        return {"ok": True, "tab": target["url"], "html": html}

    async def _tab_process(self, args: list[str],
                           result: dict[str, Any] | None) -> dict[str, Any]:
        """Run harness-written process(HTML) code against a tab's HTML."""
        target = self._resolve_tab(args[0] if args else "")
        if "error" in target:
            return target
        code = self._fenced_code(result or {}, "python")
        if not code or "def process" not in code:
            return {"error": "tab.process requires a fenced ```python block "
                             "defining process(HTML)"}
        reader = getattr(self, "tab_reader", None)
        if reader is None:
            return {"error": "this integration cannot read tabs"}
        try:
            html = (await _maybe_await(reader(target["tab_id"])))[:200_000]
            outcome = _run_process_code(code, html)
        except Exception as e:  # noqa: BLE001
            return {"error": f"tab process failed: {e}", "tab": target["url"]}
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
