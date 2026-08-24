"""Browser session tracking: windows, tabs, profiles — kept coherent.

The extension reports session events over the websocket; the CLI keeps a
model of the browser state so harness plugins (and CI/headless use later)
can query "what tabs are open / which profile" without polling.

Event shapes (extension -> CLI), all {"type": "session.*"}:

    session.hello    {profile, windows: [{id, tabs: [{id, url, title, active}]}]}
    session.tab.updated  {windowId, tabId, url, title, active}
    session.tab.removed  {windowId, tabId}
    session.window.removed {windowId}
    session.profile.changed {profile}

The same event bus is reused in reverse: the CLI emits browser.* events
(tab.new, tab.open, tab.close, doc.open) that the extension applies.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


def now() -> float:
    return time.time()


@dataclass
class Tab:
    tab_id: int
    window_id: int
    url: str = ""
    title: str = ""
    active: bool = False
    updated_at: float = field(default_factory=now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.tab_id, "windowId": self.window_id, "url": self.url,
            "title": self.title, "active": self.active,
        }


@dataclass
class Window:
    window_id: int
    profile: str = "default"
    tabs: dict[int, Tab] = field(default_factory=dict)
    updated_at: float = field(default_factory=now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.window_id, "profile": self.profile,
            "tabs": [t.to_dict() for t in self.tabs.values()],
        }


@dataclass
class Profile:
    name: str
    windows: dict[int, Window] = field(default_factory=dict)
    updated_at: float = field(default_factory=now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "windows": [w.to_dict() for w in self.windows.values()],
        }


class SessionTracker:
    """Tracks the live browser state from extension events."""

    def __init__(self) -> None:
        self.profiles: dict[str, Profile] = {}
        self.current_profile: str = "default"
        self.last_seen: float | None = None
        self.connected: bool = False

    # -- inbound events from the extension ---------------------------------
    def apply_event(self, msg: dict[str, Any]) -> None:
        """Apply one session.* event; unknown types are ignored."""
        etype = msg.get("type", "")
        self.last_seen = now()
        if etype == "session.hello":
            self._replace_all(msg)
        elif etype == "session.tab.updated":
            self._tab_updated(msg)
        elif etype == "session.tab.removed":
            self._tab_removed(msg)
        elif etype == "session.window.removed":
            self._window_removed(msg)
        elif etype == "session.profile.changed":
            self.current_profile = msg.get("profile", self.current_profile)
        elif etype == "session.goodbye":
            self.connected = False

    def _replace_all(self, msg: dict[str, Any]) -> None:
        profile = msg.get("profile", "default")
        self.current_profile = profile
        self.profiles = {}
        prof = self.profiles.setdefault(profile, Profile(name=profile))
        for w in msg.get("windows", []):
            win = Window(window_id=w["id"], profile=profile)
            prof.windows[win.window_id] = win
            for t in w.get("tabs", []):
                tab = Tab(tab_id=t["id"], window_id=win.window_id,
                          url=t.get("url", ""), title=t.get("title", ""),
                          active=t.get("active", False))
                win.tabs[tab.tab_id] = tab
        self.connected = True

    def _tab_updated(self, msg: dict[str, Any]) -> None:
        wid, tid = msg.get("windowId"), msg.get("tabId")
        if wid is None or tid is None:
            return
        prof = self._profile_for_window(wid)
        win = prof.windows.setdefault(wid, Window(window_id=wid, profile=prof.name))
        tab = win.tabs.get(tid)
        if tab is None:
            tab = win.tabs[tid] = Tab(tab_id=tid, window_id=wid)
        tab.url = msg.get("url", tab.url)
        tab.title = msg.get("title", tab.title)
        tab.active = msg.get("active", tab.active)
        tab.updated_at = now()
        if tab.active:
            for other in win.tabs.values():
                other.active = other.tab_id == tid

    def _profile_for_window(self, window_id: int) -> Profile:
        for prof in self.profiles.values():
            if window_id in prof.windows:
                return prof
        return self.profiles.setdefault(self.current_profile,
                                        Profile(name=self.current_profile))

    def _tab_removed(self, msg: dict[str, Any]) -> None:
        wid, tid = msg.get("windowId"), msg.get("tabId")
        for prof in self.profiles.values():
            win = prof.windows.get(wid)
            if win:
                win.tabs.pop(tid, None)
                return

    def _window_removed(self, msg: dict[str, Any]) -> None:
        wid = msg.get("windowId")
        for prof in self.profiles.values():
            prof.windows.pop(wid, None)

    # -- queries -------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """Full coherent state; suitable for harness prompts / CI."""
        return {
            "connected": self.connected,
            "currentProfile": self.current_profile,
            "profiles": [p.to_dict() for p in self.profiles.values()],
        }

    def active_tab(self) -> Tab | None:
        for prof in self.profiles.values():
            for win in prof.windows.values():
                for tab in win.tabs.values():
                    if tab.active:
                        return tab
        return None

    def find_tab(self, url_contains: str = "", tab_id: int | None = None) -> Tab | None:
        for prof in self.profiles.values():
            for win in prof.windows.values():
                for tab in win.tabs.values():
                    if tab_id is not None and tab.tab_id == tab_id:
                        return tab
                    if url_contains and url_contains in tab.url:
                        return tab
        return None


class EventBus:
    """Collects events the CLI wants the browser to apply.

    Each connected websocket client gets the events queued here flushed
    to it. In headless/CI mode (no browser attached) events are simply
    recorded — useful for asserting what WOULD have happened.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, etype: str, **payload: Any) -> dict[str, Any]:
        ev = {"type": etype, "ts": now(), **payload}
        self.events.append(ev)
        return ev

    def drain(self) -> list[dict[str, Any]]:
        out, self.events = self.events, []
        return out

    def tab_new(self, url: str, window_id: int | None = None,
                active: bool = True) -> dict:
        return self.emit("tab.new", url=url, windowId=window_id, active=active)

    def tab_open(self, url: str) -> dict:
        """Alias kept for compatibility with the spec naming."""
        return self.emit("tab.open", url=url)

    def tab_close(self, tab_id: int) -> dict:
        return self.emit("tab.close", tabId=tab_id)

    def doc_open(self, url: str) -> dict:
        return self.emit("doc.open", url=url)
