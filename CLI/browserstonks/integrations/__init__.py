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
            resp = await self.chat_handler.handle(msg)
            resp["requestId"] = msg.get("requestId")
            # flush queued browser events to this client
            if self.bus is not None:
                resp.setdefault("events", self.bus.drain())
            return resp

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

        if mtype == "ping":
            return {"type": "pong"}

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
                 port: int = 8765) -> None:
        super().__init__(planner)
        self.host = host
        self.port = port

    async def start(self) -> None:
        import websockets

        async def on_conn(ws):
            print(f"[ws] client connected: {ws.remote_address}", flush=True)
            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        await ws.send(json.dumps({"error": "bad json"}))
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
                            if "tabId" not in resp and msg.get("tabId") is not None:
                                resp["tabId"] = msg.get("tabId")
                        await ws.send(json.dumps(resp))
                        self._log_msg(msg, resp)
            finally:
                print(f"[ws] client disconnected: {ws.remote_address}", flush=True)

        async with websockets.serve(on_conn, self.host, self.port):
            print(f"[ws] BrowserStonks listening on ws://{self.host}:{self.port}",
                  flush=True)
            await asyncio.Future()  # run forever

    @staticmethod
    def _log_msg(msg: dict, resp: dict) -> None:
        mtype = msg.get("type", "?")
        rid = msg.get("requestId") or ""
        if mtype == "plan":
            dom = msg.get("dom", {})
            n = sum(len(f.get("fields", [])) for f in dom.get("forms", []))
            print(f"[ws] plan {rid}: {dom.get('url', '?')} ({n} fields) -> "
                  f"{len(resp.get('values', {}))} values", flush=True)
        elif mtype == "chat":
            applied = resp.get("actionsApplied", [])
            print(f"[ws] chat {rid} [{msg.get('harness')}/{msg.get('sessionId')}]: "
                  f"{len(applied)} action(s) applied", flush=True)
        elif not mtype.startswith("session."):
            print(f"[ws] {mtype} {rid}", flush=True)


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
