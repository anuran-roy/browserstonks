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

import re
from pathlib import Path
from typing import Any

from .harnesses import HarnessManager, HarnessError
from .session import EventBus, SessionTracker


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
        result = self.harnesses.chat(harness, message, session_id, context)
        applied = self._apply_actions(result.get("actions", []), message, result)

        return {
            "type": "chat.response",
            "harness": harness,
            "session": result.get("session", {"harness": harness, "id": session_id}),
            "reply": result.get("reply", ""),
            "actionsApplied": applied,
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
    def _apply_actions(self, actions: list[dict[str, Any]],
                       original_message: str = "",
                       result: dict[str, Any] | None = None) -> list[dict[str, Any]]:
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
