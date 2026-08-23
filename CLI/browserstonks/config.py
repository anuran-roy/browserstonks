"""Config loading: config.jsonc + config.schema.jsonc + .env support.

- config.jsonc is JSON with // and /* */ comments (parsed with json5 if
  available, falling back to a regex comment stripper).
- .env is loaded before integrations start; values support ${VAR} expansion.
- config.schema.jsonc documents allowed fields; `browserstonks doctor`
  validates config against it.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    import json5  # type: ignore
    _HAVE_JSON5 = True
except ImportError:
    _HAVE_JSON5 = False

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")
_COMMENT_RE = re.compile(r"^\s*(//|#).*$|/\*.*?\*/", re.MULTILINE | re.DOTALL)


def strip_comments(text: str) -> str:
    if _HAVE_JSON5:
        try:
            return json.dumps(json5.loads(text))
        except Exception:
            pass
    # Crude fallback: remove // and /* */ comments that are not inside strings.
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def loads_jsonc(text: str) -> Any:
    return json.loads(strip_comments(text))


def load_dotenv(path: Path, override: bool = False) -> dict[str, str]:
    """Minimal .env loader. KEY=VALUE lines, quotes stripped, # comments."""
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if val[:1] in {'"', "'"} and val[-1:] == val[:1]:
            val = val[1:-1]
        if override or key not in os.environ:
            os.environ[key] = val
        loaded[key] = os.environ.get(key, val)
    return loaded


def expand_env(value: Any, missing: list[str] | None = None) -> Any:
    """Recursively expand ${VAR} in string values of the config."""
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            var = m.group(1)
            v = os.environ.get(var)
            if v is None and missing is not None:
                missing.append(var)
            return v or m.group(0)
        return _ENV_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: expand_env(v, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v, missing) for v in value]
    return value


class Config:
    def __init__(self, root: Path):
        self.root = root
        self.config_path = root / "config.jsonc"
        self.schema_path = root / "config.schema.jsonc"
        self.env_path = root / ".env"
        self.env: dict[str, str] = {}
        self.raw: dict[str, Any] = {}
        self.data: dict[str, Any] = {}
        self.missing_env: list[str] = []

    def load(self) -> "Config":
        if self.env_path.exists():
            self.env = load_dotenv(self.env_path)
        if self.config_path.exists():
            self.raw = loads_jsonc(self.config_path.read_text())
        self.data = expand_env(self.raw, self.missing_env)
        return self

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    # ---- schema validation ------------------------------------------------
    def schema(self) -> dict[str, Any] | None:
        if not self.schema_path.exists():
            return None
        return loads_jsonc(self.schema_path.read_text())

    def validate(self) -> list[str]:
        """Light structural validation against config.schema.jsonc.

        We implement a pragmatic subset of JSON Schema: type, required,
        properties, additionalProperties, enum. Enough to catch typos and
        misplaced keys in config.jsonc at startup / `doctor` time.
        """
        errors: list[str] = []

        def check(node: Any, schema: dict, path: str) -> None:
            t = schema.get("type")
            type_map = {
                "object": dict, "array": list, "string": str,
                "number": (int, float), "boolean": bool, "integer": int,
            }
            if t and t in type_map:
                if not isinstance(node, type_map[t]) or (
                    t == "integer" and isinstance(node, bool)
                ):
                    errors.append(f"{path}: expected {t}, got {type(node).__name__}")
                    return
            if "enum" in schema and node not in schema["enum"]:
                errors.append(f"{path}: {node!r} not in enum {schema['enum']}")
            if isinstance(node, dict):
                for req in schema.get("required", []):
                    if req not in node:
                        errors.append(f"{path}: missing required field '{req}'")
                props = schema.get("properties", {})
                addl = schema.get("additionalProperties", True)
                for k, v in node.items():
                    if k in props:
                        check(v, props[k], f"{path}.{k}")
                    elif not addl:
                        errors.append(f"{path}: unknown field '{k}'")
            if isinstance(node, list) and "items" in schema:
                for i, item in enumerate(node):
                    check(item, schema["items"], f"{path}[{i}]")

        sch = self.schema()
        if sch:
            check(self.data, sch, "config")
        return errors


def default_root() -> Path:
    env = os.environ.get("BROWSERSTONKS_HOME")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent.parent
