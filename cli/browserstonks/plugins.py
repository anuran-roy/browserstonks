"""Dynamic plugin system.

Two plugin families, both discovered the same way:

1. Integrations  — transports that connect an external client (Chrome
   extension, other tools) to the planner. Built-ins: websocket, stdio.
2. Form adapters — site-specific form detection (Google Forms, Typeform,
   generic HTML). Plug-n-play: any package exposing the group name is found.

Discovery order:
  a) local `plugins/` directory inside the CLI root (drop-in .py files)
  b) installed packages advertising the `browserstonks_integrations` /
     `browserstonks_form_adapters` entry-point groups
  c) built-ins shipped in this package

An integration plugin registers:  ENTRYPOINTS["browserstonks_integrations"] = {"name": Class}
A form-adapter plugin registers:   ENTRYPOINTS["browserstonks_form_adapters"] = {"name": Class}
"""
from __future__ import annotations

import importlib
import importlib.metadata
import pkgutil
from pathlib import Path
from typing import Type

from .logging_util import log as _log

ENTRYPOINT_GROUPS = [
    "browserstonks_integrations",
    "browserstonks_form_adapters",
    "browserstonks_harnesses",
]


class PluginRegistry:
    def __init__(self) -> None:
        self._integrations: dict[str, type] = {}
        self._adapters: dict[str, type] = {}
        self._harnesses: dict[str, type] = {}

    # -- registration -------------------------------------------------------
    def register_integration(self, name: str, cls: type) -> None:
        self._integrations[name] = cls

    def register_adapter(self, name: str, cls: type) -> None:
        self._adapters[name] = cls

    def register_harness(self, name: str, cls: type) -> None:
        self._harnesses[name] = cls

    # -- lookup -------------------------------------------------------------
    def integration(self, name: str) -> type:
        if name not in self._integrations:
            raise KeyError(
                f"unknown integration '{name}'. available: {sorted(self._integrations)}"
            )
        return self._integrations[name]

    def adapter(self, name: str) -> type:
        if name not in self._adapters:
            raise KeyError(
                f"unknown form adapter '{name}'. available: {sorted(self._adapters)}"
            )
        return self._adapters[name]

    @property
    def integrations(self) -> dict[str, type]:
        return dict(self._integrations)

    @property
    def adapters(self) -> dict[str, type]:
        return dict(self._adapters)

    @property
    def harnesses(self) -> dict[str, type]:
        return dict(self._harnesses)

    # -- discovery ----------------------------------------------------------
    def load_local_plugins(self, plugins_dir: Path) -> list[str]:
        """Import every .py in <root>/plugins/ and pick up its ENTRYPOINTS."""
        loaded: list[str] = []
        if not plugins_dir.is_dir():
            return loaded
        import sys
        sys.path.insert(0, str(plugins_dir))
        try:
            for mod_info in sorted(Path(plugins_dir).glob("*.py")):
                if mod_info.name.startswith("_"):
                    continue
                mod = importlib.import_module(mod_info.stem)
                self._absorb_module(mod)
                loaded.append(mod_info.stem)
        finally:
            sys.path.remove(str(plugins_dir))
        return loaded

    def load_entrypoints(self) -> list[str]:
        loaded: list[str] = []
        for group in ENTRYPOINT_GROUPS:
            try:
                eps = importlib.metadata.entry_points(group=group)
            except Exception:
                continue
            for ep in eps:
                try:
                    self._absorb_module(ep.load())
                    loaded.append(f"{group}:{ep.name}")
                except Exception as e:  # noqa: BLE001
                    print(f"[plugins] failed to load {group}:{ep.name}: {e}")
        return loaded

    def load_builtins(self) -> None:
        from .integrations import BUILTIN_INTEGRATIONS
        from .form_adapters import BUILTIN_ADAPTERS
        for name, cls in BUILTIN_INTEGRATIONS.items():
            self.register_integration(name, cls)
        for name, cls in BUILTIN_ADAPTERS.items():
            self.register_adapter(name, cls)
        self._plog("loaded built-ins: integrations=%s adapters=%s harnesses="
                   "(via HarnessManager)",
                   sorted(BUILTIN_INTEGRATIONS), sorted(BUILTIN_ADAPTERS))

    def _plog(self, msg: str, *args) -> None:
        from .logging_util import log as _log
        _log("plugins").debug(msg, *args)

    def _absorb_module(self, mod) -> None:
        table = getattr(mod, "ENTRYPOINTS", None)
        if not table:
            return
        for group, entries in table.items():
            for name, cls in entries.items():
                if group == "browserstonks_integrations":
                    self.register_integration(name, cls)
                elif group == "browserstonks_form_adapters":
                    self.register_adapter(name, cls)
                elif group == "browserstonks_harnesses":
                    self.register_harness(name, cls)


_registry: PluginRegistry | None = None


def get_registry(plugins_dir: Path | None = None) -> PluginRegistry:
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
        _registry.load_builtins()
        loaded = []
        if plugins_dir is not None:
            loaded = _registry.load_local_plugins(plugins_dir)
            if loaded:
                _log("plugins").info("local plugins loaded: %s", loaded)
        _registry.load_entrypoints()
    return _registry
