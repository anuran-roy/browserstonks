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

    def __init__(self, planner: Planner) -> None:
        self.planner = planner

    async def start(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def handle(self, dom: dict[str, Any]) -> dict[str, Any]:
        """Shared request handling: describe the form, plan the program."""
        adapter = extract_site_adapter(dom)
        description = adapter.describe(dom)
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
                    if msg.get("type") == "plan":
                        dom = msg.get("dom", {})
                        n_fields = sum(
                            len(f.get("fields", []))
                            for f in dom.get("forms", []))
                        print(f"[ws] plan request {msg.get('requestId')} "
                              f"from {msg.get('sender')}: "
                              f"{dom.get('url', '?')} "
                              f"({n_fields} fields)", flush=True)
                        try:
                            resp = await self.handle(dom)
                        except Exception as e:  # noqa: BLE001
                            import traceback
                            traceback.print_exc()
                            await ws.send(json.dumps({
                                "error": f"planner failed: {e}",
                                "requestId": msg.get("requestId"),
                            }))
                            continue
                        # echo routing fields so clients (e.g. the Chrome
                        # extension service worker) can match the reply
                        resp["requestId"] = msg.get("requestId")
                        resp["sender"] = msg.get("sender")
                        resp["tabId"] = msg.get("tabId") or dom.get("tabId")
                        await ws.send(json.dumps(resp))
                        print(f"[ws] plan response {msg.get('requestId')} sent "
                              f"({len(resp.get('values', {}))} values, "
                              f"{len(resp.get('program', ''))} chars)", flush=True)
                    elif msg.get("type") == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                    else:
                        await ws.send(json.dumps({"error": "unknown type"}))
            finally:
                print(f"[ws] client disconnected: {ws.remote_address}")

        async with websockets.serve(on_conn, self.host, self.port):
            print(f"[ws] BrowserStonks listening on ws://{self.host}:{self.port}")
            await asyncio.Future()  # run forever


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
            if msg.get("type") == "plan":
                resp = await self.handle(msg.get("dom", {}))
                print(json.dumps(resp), flush=True)
            else:
                print(json.dumps({"error": "unknown type"}), flush=True)


BUILTIN_INTEGRATIONS: dict[str, type[BaseIntegration]] = {
    "websocket": WebSocketIntegration,
    "stdio": StdioIntegration,
}
