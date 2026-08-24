"""Integrations: transports connecting external clients to the planner.

The Chrome extension is the first integration (websocket). stdio is the
second, showing the plug-in pattern. Both speak the same message shape:

    request:  {"type": "plan", "dom": {...}, "client": "chrome-extension@1"}
    response: {"program": "...", "values": {...}, "site": "..."}

Add a new integration by subclassing BaseIntegration and registering it
(see plugins.py). Built-ins here; drop-ins in <root>/plugins/.
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from ..planner import Planner
from ..form_adapters import extract_site_adapter
from ..logging_util import log as _log

_ws_log = _log("ws")
_dbg = _ws_log.debug


class BaseIntegration:
    name = "base"

    def __init__(self, planner: Planner, chat_handler=None,
                 tracker=None, bus=None) -> None:
        self.planner = planner
        self.chat_handler = chat_handler
        self.tracker = tracker
        self.bus = bus

    async def start(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def dispatch(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """Route a client message: plan / chat / session.* / ping."""
        mtype = msg.get("type", "")
        _dbg("dispatch %s (requestId=%s)", mtype, msg.get("requestId"))

        # tab HTML replies (fufilled by the extension asynchronously)
        if mtype == "tab.read.response":
            fut = self._read_futures.get(msg.get("requestId", ""))
            if fut and not fut.done():
                if msg.get("error"):
                    fut.set_exception(RuntimeError(str(msg["error"])))
                else:
                    fut.set_result(msg.get("html", ""))
            return None  # no response needed

        if mtype == "plan":
            dom = msg.get("dom", {})
            description = extract_site_adapter(dom).describe(dom)
            plan = await self.planner.plan(description)
            plan["client"] = dom.get("client", "unknown")
            plan["requestId"] = msg.get("requestId")
            plan["sender"] = msg.get("sender")
            plan["tabId"] = msg.get("tabId") or dom.get("tabId")
            return plan

        if mtype == "chat" and self.chat_handler is not None:
            async def _emit(ev: dict) -> None:
                await self._send(ev)

            resp = await self.chat_handler.handle(msg, emit=_emit)
            resp["requestId"] = msg.get("requestId")
            # flush queued browser events to this client
            if self.bus is not None:
                resp.setdefault("events", self.bus.drain())
            return resp

        if mtype == "session.snapshot" and self.tracker is not None:
            return {"type": "session.snapshot",
                    "snapshot": self.tracker.snapshot()}

        if mtype.startswith("session.") and self.tracker is not None:
            self.tracker.apply_event(msg)
            return {"type": "session.ack", "event": mtype}

        if mtype == "browser.events.drain" and self.bus is not None:
            return {"type": "browser.events", "events": self.bus.drain()}

        if mtype == "harnesses.list" and self.chat_handler is not None:
            return {"type": "harnesses.list",
                    "harnesses": self.chat_handler.harnesses.list_harnesses()}

        if mtype == "session.snapshot" and self.tracker is not None:
            return {"type": "session.snapshot",
                    "snapshot": self.tracker.snapshot()}

        elif mtype == "ping":
            return {"type": "pong"}

        # session.ack and other quiet replies don't need echoing/log spam
        return {"error": f"unknown message type '{mtype}'"}

    async def handle(self, dom: dict[str, Any]) -> dict[str, Any]:
        """Legacy single-purpose entry: describe the form, plan the program."""
        description = extract_site_adapter(dom).describe(dom)
        plan = await self.planner.plan(description)
        plan["client"] = dom.get("client", "unknown")
        return plan


class WebSocketIntegration(BaseIntegration):
    """Serves the Chrome extension. ws://127.0.0.1:8765 by default."""
    name = "websocket"

    def __init__(self, planner: Planner, host: str = "127.0.0.1",
                 port: int = 8765, chat_handler=None, tracker=None,
                 bus=None) -> None:
        super().__init__(planner, chat_handler=chat_handler,
                         tracker=tracker, bus=bus)
        self.host = host
        self.port = port
        self._conn = None
        self._read_futures: dict[str, asyncio.Future] = {}
        # serialize ALL sends: websockets raises/corrupts on concurrent
        # send() calls from different tasks (progress emits from executor
        # threads race with dispatch replies otherwise)
        self._send_lock = asyncio.Lock()

    async def _send(self, payload: dict) -> None:
        ws = self._conn
        if ws is None:
            return
        async with self._send_lock:
            await ws.send(json.dumps(payload))

    async def start(self) -> None:
        import websockets

        async def on_conn(ws):
            _ws_log.info("client connected %s", ws.remote_address)
            self._conn = ws
            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        await self._send({"error": "bad json"})
                        continue
                    try:
                        resp = await self.dispatch(msg)
                    except Exception as e:  # noqa: BLE001
                        import traceback
                        traceback.print_exc()
                        resp = {"error": f"handler failed: {e}",
                                "requestId": msg.get("requestId")}
                    if resp is not None:
                        # echo routing fields so clients (e.g. the Chrome
                        # extension service worker) can match the reply
                        if isinstance(resp, dict):
                            resp.setdefault("sender", msg.get("sender"))
                            if msg.get("requestId") is not None:
                                resp.setdefault("requestId", msg.get("requestId"))
                            if "tabId" not in resp and msg.get("tabId") is not None:
                                resp["tabId"] = msg.get("tabId")
                        try:
                            await self._send(resp)
                        except Exception:  # noqa: BLE001 - client vanished
                            _ws_log.info("client went away mid-reply %s",
                                         ws.remote_address)
                            break
                        self._log_msg(msg, resp)
            except Exception as e:  # noqa: BLE001 - normal disconnects
                # Chrome suspending the MV3 worker drops the socket with a
                # reset/close; it reconnects on its own — not an error
                _ws_log.info("connection closed (%s: %s); expecting reconnect",
                             type(e).__name__, str(e)[:80])
            finally:
                self._conn = None
                _ws_log.info("client disconnected %s", ws.remote_address)

        async with websockets.serve(
                on_conn, self.host, self.port,
                # The extension runs its own app-level keepalive (a JSON
                # ping every 20s). The library's protocol-level keepalive
                # kills connections when Chrome suspends the MV3 worker
                # (protocol pings don't reset that), so disable it.
                ping_interval=None, ping_timeout=None):
            _ws_log.info("listening on ws://%s:%s", self.host, self.port)
            await asyncio.Future()  # run forever

    async def request_tab_html(self, tab_id: int, timeout: float = 20) -> str:
        """Ask the connected extension for a tab's HTML (tab.read support)."""
        _dbg("requesting tab html for tab %s", tab_id)
        ws = self._conn
        if ws is None:
            raise RuntimeError("no browser connected")
        rid = f"readhtml_{tab_id}_{id(self):x}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._read_futures[rid] = fut
        await self._send({
            "type": "tab.read.request", "tabId": tab_id, "requestId": rid,
        })
        try:
            html = await asyncio.wait_for(fut, timeout)
            _dbg("tab html received for tab %s (%d chars)", tab_id, len(html))
            return html
        except asyncio.TimeoutError:
            _ws_log.warning("tab html request for tab %s timed out (%ss)",
                            tab_id, timeout)
            raise
        finally:
            self._read_futures.pop(rid, None)

    @staticmethod
    def _log_msg(msg: dict, resp: dict) -> None:
        mtype = msg.get("type", "?")
        rid = msg.get("requestId") or ""
        if mtype == "plan":
            dom = msg.get("dom", {})
            n = sum(len(f.get("fields", [])) for f in dom.get("forms", []))
            _ws_log.info("plan %s: %s (%d fields) -> %d values",
                         rid, dom.get("url", "?"), n,
                         len(resp.get("values", {})))
        elif mtype == "chat":
            applied = resp.get("actionsApplied", [])
            ops = ", ".join(f"{a['op']}:{'ok' if a.get('ok') else 'FAIL'}"
                            for a in applied) or "none"
            _ws_log.info("chat %s [%s/%s]: reply %d chars, actions: %s",
                         rid, msg.get("harness"), msg.get("sessionId"),
                         len(resp.get("reply", "")), ops)
        elif mtype.startswith("session."):
            _dbg("session event %s applied", mtype)
        else:
            _ws_log.info("%s %s", mtype, rid)


class StdioIntegration(BaseIntegration):
    """Line-delimited JSON on stdin/stdout. Good for testing and pipes."""
    name = "stdio"

    async def start(self) -> None:
        print("[stdio] ready; send JSON lines (Ctrl-D to quit)", file=sys.stderr)
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                print(json.dumps({"error": "bad json"}), flush=True)
                continue
            resp = await self.dispatch(msg)
            if resp is not None:
                print(json.dumps(resp), flush=True)


BUILTIN_INTEGRATIONS: dict[str, type[BaseIntegration]] = {
    "websocket": WebSocketIntegration,
    "stdio": StdioIntegration,
}
