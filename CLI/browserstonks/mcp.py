"""Custom MCP (Model Context Protocol) support.

config.jsonc:

    "mcpServers": {
      "my-tools": {
        "command": "npx",
        "args": ["-y", "@example/mcp-server"],
        "env": { "FOO": "bar" },
        "enabled": true
      }
    }

Each enabled server is launched as a subprocess speaking JSON-RPC over
stdio (MCP's default transport). Tools are listed and made available to
the planner; calling them is exposed via call_tool().
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any


class MCPServer:
    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict[str, Any]] = []
        self._id = 0

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        full_env = {**os.environ, **self.env}
        self.proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=full_env,
        )
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "browserstonks", "version": "0.1.0"},
        })
        self._notify("notifications/initialized", {})
        self.tools = self._rpc("tools/list", {}).get("tools", [])

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    # -- JSON-RPC over stdio ------------------------------------------------
    def _send(self, payload: dict) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(payload) + "\n").encode())
        self.proc.stdin.flush()

    def _recv(self) -> dict:
        assert self.proc and self.proc.stdout
        line = self.proc.stdout.readline()
        if not line:
            raise ConnectionError(f"MCP server '{self.name}' closed stdout")
        return json.loads(line)

    def _rpc(self, method: str, params: dict) -> dict:
        self._id += 1
        req_id = self._id
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method,
                    "params": params})
        while True:
            msg = self._recv()
            if msg.get("id") == req_id:
                if "error" in msg:
                    raise RuntimeError(
                        f"MCP {self.name}.{method}: {msg['error']}")
                return msg.get("result", {})

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # -- public API ----------------------------------------------------------
    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        result = self._rpc("tools/call", {
            "name": tool_name, "arguments": arguments})
        content = result.get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts) if texts else result


def load_mcp_servers(config_data: dict[str, Any]) -> dict[str, MCPServer]:
    servers: dict[str, MCPServer] = {}
    for name, spec in (config_data.get("mcpServers") or {}).items():
        if not spec.get("enabled", True):
            continue
        servers[name] = MCPServer(
            name=name,
            command=spec["command"],
            args=spec.get("args", []),
            env=spec.get("env", {}),
        )
    return servers


class MCPManager:
    """Starts/stops configured servers; exposes tools to the planner."""

    def __init__(self, config_data: dict[str, Any]) -> None:
        self.servers = load_mcp_servers(config_data)

    def start_all(self) -> None:
        for srv in self.servers.values():
            try:
                srv.start()
                print(f"[mcp] started '{srv.name}' "
                      f"({len(srv.tools)} tools)")
            except Exception as e:  # noqa: BLE001
                print(f"[mcp] failed to start '{srv.name}': {e}")

    def stop_all(self) -> None:
        for srv in self.servers.values():
            srv.stop()

    def tool_catalog(self) -> list[dict[str, Any]]:
        out = []
        for srv in self.servers.values():
            for tool in srv.tools:
                out.append({
                    "server": srv.name,
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                })
        return out

    def call(self, server: str, tool: str, arguments: dict) -> Any:
        return self.servers[server].call_tool(tool, arguments)
