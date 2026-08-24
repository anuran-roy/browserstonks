"""Chat session persistence: save/load conversation transcripts.

Sessions are stored as JSON files under <config root>/sessions/:

    sessions/
      2026-08-24_22-30-05_sp-mtabc8.json
      ...

Each file:
    {
      "id": "sp-mtabc8",            # client-chosen session id
      "harness": "opencode",
      "title": "first user message (truncated)",
      "created":  1787590205.1,
      "updated":  1787590205.1,
      "messages": [
        {"role": "user", "content": "...", "ts": ...},
        {"role": "assistant", "content": "...", "ts": ...,
         "actions": [...], "reasoning": "..."}
      ]
    }

Deleting the file deletes the session (harness-side sessions like the
OpenCode server session are independent; the CLI just forgets them).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .logging_util import log as _log

_ss_log = _log("sessions")

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class SessionStore:
    """File-backed chat session store."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ files
    def _path_for(self, sid: str) -> Path:
        safe = _SAFE.sub("_", sid)[:80] or "session"
        return self.root / f"{safe}.json"

    def _all_files(self) -> list[Path]:
        return sorted(self.root.glob("*.json"),
                      key=lambda p: p.stat().st_mtime, reverse=True)

    # ------------------------------------------------------------------- api
    def save(self, sid: str, harness: str, messages: list[dict]) -> None:
        """Upsert a session file from the message list."""
        if not sid:
            return
        path = self._path_for(sid)
        try:
            old = self.load(sid) or {}
            created = old.get("created", time.time())
            title = ""
            for m in messages:
                if m.get("role") == "user" and m.get("content"):
                    title = m["content"].strip()[:60]
                    break
            data = {
                "id": sid,
                "harness": harness,
                "title": title or (old.get("title") or "session"),
                "created": created,
                "updated": time.time(),
                "messages": messages,
            }
            path.write_text(json.dumps(data, indent=1))
            _ss_log.debug("saved session %s (%d messages)", sid, len(messages))
        except OSError as e:
            _ss_log.warning("failed saving session %s: %s", sid, e)

    def load(self, sid: str) -> dict[str, Any] | None:
        path = self._path_for(sid)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            _ss_log.warning("failed loading session %s: %s", sid, e)
            return None

    def delete(self, sid: str) -> bool:
        path = self._path_for(sid)
        if path.exists():
            path.unlink()
            _ss_log.info("deleted session %s", sid)
            return True
        return False

    def list(self) -> list[dict[str, Any]]:
        """Summaries, newest first, for the session picker."""
        out = []
        for p in self._all_files():
            try:
                d = json.loads(p.read_text())
                out.append({
                    "id": d.get("id", p.stem),
                    "harness": d.get("harness", "?"),
                    "title": d.get("title", p.stem),
                    "updated": d.get("updated", p.stat().st_mtime),
                    "messages": len(d.get("messages", [])),
                })
            except (OSError, json.JSONDecodeError):
                continue
        return out
