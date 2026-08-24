"""BrowserStonks CLI entry point.

Commands:
    init     -- write config.jsonc, config.schema.jsonc, .env, answers.jsonc
    serve    -- load config, start MCP servers + enabled integrations
    plan     -- one-shot: read a DOM snapshot JSON file, print the plan
    doctor   -- validate config against schema, check env keys / MCP servers
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import __version__
from .logging_util import setup_logging, log as get_logger
from .config import Config, loads_jsonc
from .llm import make_provider, LLMError
from .mcp import MCPManager
from .planner import Planner, load_rulebook
from .plugins import get_registry

ROOT = Path(__file__).resolve().parent.parent

INIT_CONFIG = """\
// BrowserStonks configuration (JSON with comments).
// String values support ${VAR} expansion from the environment / .env file.
{
  "llm": {
    // provider: "openrouter" | "openai" | "anthropic" | "offline"
    "provider": "${LLM_PROVIDER}",
    "model": "${LLM_MODEL}",
    // api key: prefer .env and reference it here
    "api_key": "${LLM_API_KEY}"
  },
  "integrations": {
    "websocket": { "enabled": true, "host": "127.0.0.1", "port": 8765 },
    "stdio": { "enabled": false }
  },
  "formAdapters": {
    // plug-n-play site adapters; disable to skip a built-in
    "generic": true,
    "google_forms": true,
    "typeform": true
  },
  "answersFile": "answers.jsonc",
  "docsDir": "docs",
  // managed storage for generated docs (media/<sessionId>/ locally;
  // uploads when enabled). provider: cloudinary (default) | s3 | gcs
  "storage": { "enabled": false, "provider": "cloudinary" },
  // connection-token auth: issue with `browserstonks tokens issue`
  "auth": { "required": false },
  "harnesses": {
    // Agent harnesses used by the sidepanel chat. Local = CLI binary,
    // server = HTTP endpoint. auth configures secure-server auth.
    "opencode": {
      // server mode (verified protocol):
      "base_url": "http://127.0.0.1:4096",
      // "agent": "build",
      // "model": { "providerID": "opencode", "modelID": "..." },
      // "auth": { "type": "bearer", "token": "${OPENCODE_TOKEN}" }
      // local mode instead: "command": "opencode", "workdir": "."
    },
    "openclaw": {
      // "command": "openclaw",
      // "base_url": "http://127.0.0.1:5000",
      // "auth": { "type": "apikey", "token": "${OPENCLAW_API_KEY}", "header": "X-Api-Key" }
    },
    "hermes": {
      // "command": "hermes",
      // "base_url": "http://127.0.0.1:8080",
      // "auth": { "type": "basic", "username": "${HERMES_USER}", "password": "${HERMES_PASS}" }
    }
  },
  "mcpServers": {
    // "example": {
    //   "command": "npx", "args": ["-y", "@example/mcp-server"],
    //   "env": {}, "enabled": true
    // }
  }
}
"""

INIT_ENV = """\
# BrowserStonks environment — loaded before integrations start.
LLM_PROVIDER=openrouter
LLM_MODEL=openai/gpt-4o-mini
LLM_API_KEY=
# OPENAI_API_KEY=
# ANTHROPIC_API_KEY=
"""

INIT_ANSWERS = """\
// BrowserStonks rulebook: repeated answers matched by site / urlPattern.
{
  "defaults": {},
  "rules": [
    // {
    //   "site": "google_forms",
    //   "urlPattern": "docs\\\\.google\\\\.com",
    //   "values": { "your name": "Anuran" }
    // }
  ]
}
"""

INIT_SCHEMA = Path(__file__).resolve().parent / "config.schema.jsonc"


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugins").mkdir(exist_ok=True)

    targets = {
        "config.jsonc": INIT_CONFIG,
        ".env": INIT_ENV,
        "answers.jsonc": INIT_ANSWERS,
    }
    for name, content in targets.items():
        p = root / name
        if p.exists() and not args.force:
            print(f"skip {name} (exists; use --force to overwrite)")
            continue
        p.write_text(content)
        print(f"wrote {p}")

    schema_dst = root / "config.schema.jsonc"
    if not schema_dst.exists() or args.force:
        schema_dst.write_text((ROOT / "browserstonks" / "config.schema.jsonc").read_text())
        print(f"wrote {schema_dst}")
    return 0


def _build_planner(cfg: Config) -> Planner:
    rulebook = load_rulebook(cfg.root / cfg.get("answersFile", "answers.jsonc"))
    provider = None
    llm_cfg = cfg.get("llm")
    if llm_cfg and llm_cfg.get("provider") not in (None, "", "offline"):
        try:
            provider = make_provider(llm_cfg)
        except LLMError as e:
            get_logger("config").warning("%s (falling back to rulebook only)", e)
    return Planner(rulebook=rulebook, provider=provider)


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = Config(Path(args.root)).load()

    # keep the root config.schema.jsonc in sync with the packaged one so
    # validation never fails on fields added by newer CLI versions
    packaged = INIT_SCHEMA
    local_schema = cfg.root / "config.schema.jsonc"
    if not local_schema.exists() or local_schema.read_text() != packaged.read_text():
        local_schema.write_text(packaged.read_text())
        get_logger("config").info("updated config.schema.jsonc to current CLI schema")

    errors = cfg.validate()
    if errors:
        get_logger("config").error("config validation failed:")
        for e in errors:
            get_logger("config").error("  - %s", e)
        return 1
    if cfg.missing_env:
        get_logger("config").warning(
            "unset variables referenced in config: %s", cfg.missing_env)

    mcp = MCPManager(cfg.data)
    mcp.start_all()

    planner = _build_planner(cfg)
    registry = get_registry(cfg.root / "plugins")

    # chat / harness / browser-session pipeline for the sidepanel
    from .session import SessionTracker, EventBus
    from .harnesses import HarnessManager
    from .chat import ChatHandler
    from .storage import StorageManager
    from .auth import TokenStore
    tracker = SessionTracker()
    bus = EventBus()
    harnesses = HarnessManager(cfg.data,
                               extra_harnesses=registry.harnesses)
    chat = ChatHandler(harnesses, tracker, bus,
                       docs_dir=cfg.root / cfg.get("docsDir", "docs"))
    # managed-storage uploads (doc.create publishes to cloud/s3/gcs when
    # storage.enabled is set; local media/ copy is always written first)
    chat.storage = StorageManager(cfg.get("storage") or {}, cfg.env or {})
    # connection-token auth (websocket auth gate)
    auth_cfg = cfg.get("auth") or {}
    auth_store = None
    if auth_cfg.get("required"):
        auth_store = TokenStore(auth_cfg.get("database"), root=cfg.root)
        get_logger("auth").info("token auth REQUIRED (db: %s)",
                                auth_cfg.get("database", "sqlite tokens.db"))

    enabled = []
    for name, spec in (cfg.get("integrations") or {}).items():
        if not spec.get("enabled", False):
            continue
        cls = registry.integration(name)
        if name == "websocket":
            integration = cls(planner, chat_handler=chat,
                              tracker=tracker, bus=bus,
                              host=spec.get("host", "127.0.0.1"),
                              port=int(spec.get("port", 8765)),
                              auth_store=auth_store)
            # wire the tab HTML reader for harness tab.read/tab.process
            chat.tab_reader = integration.request_tab_html
            enabled.append(integration)
        else:
            enabled.append(cls(planner, chat_handler=chat,
                               tracker=tracker, bus=bus))

    if not enabled:
        get_logger("ws").error("no integrations enabled in config.jsonc")
        return 1

    async def run_all() -> None:
        try:
            await asyncio.gather(*(i.start() for i in enabled))
        finally:
            mcp.stop_all()
            harnesses.stop_all()

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        print("\nbye")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    import asyncio
    cfg = Config(Path(args.root)).load()
    dom = json.loads(Path(args.dom).read_text())
    planner = _build_planner(cfg)
    print(json.dumps(asyncio.run(planner.plan(_describe(dom))), indent=2))
    return 0


def _describe(dom: dict) -> dict:
    from .form_adapters import extract_site_adapter
    return extract_site_adapter(dom).describe(dom)


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = Config(Path(args.root)).load()
    ok = True

    errors = cfg.validate()
    if errors:
        ok = False
        print("config errors:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("config.jsonc: valid against config.schema.jsonc")

    if cfg.missing_env:
        ok = False
        print(f"env: missing variables: {cfg.missing_env}")
    else:
        print("env: all referenced variables set")

    llm_cfg = cfg.get("llm") or {}
    if llm_cfg.get("provider", "offline") == "offline":
        print("llm: offline (rulebook only)")
    else:
        try:
            make_provider(llm_cfg)
            print(f"llm: {llm_cfg.get('provider')}/{llm_cfg.get('model')} configured")
        except LLMError as e:
            ok = False
            print(f"llm: {e}")

    registry = get_registry(cfg.root / "plugins")
    print(f"integrations: {sorted(registry.integrations)}")
    print(f"form adapters: {sorted(registry.adapters)}")

    mcp = MCPManager(cfg.data)
    if mcp.servers:
        for name in mcp.servers:
            print(f"mcp: '{name}' configured (start test skipped in doctor)")
    else:
        print("mcp: none configured")
    return 0 if ok else 1


def _token_store(args: argparse.Namespace):
    from .auth import TokenStore
    cfg = Config(Path(args.root)).load()
    auth_cfg = cfg.get("auth") or {}
    return TokenStore(auth_cfg.get("database"), root=cfg.root), cfg


def cmd_tokens_issue(args: argparse.Namespace) -> int:
    # the token is printed to stdout for copy/paste or capture; keep
    # log lines OFF stdout so they never pollute it
    import logging as _logging
    import sys as _sys
    from .logging_util import log as _log
    _log("auth").setLevel(_logging.CRITICAL)  # silence INFO lines
    store, _cfg = _token_store(args)
    token = store.issue(args.label)
    print(token)
    print("(shown once — store it now; only its hash is saved)",
          file=_sys.stderr)
    return 0


def cmd_tokens_revoke(args: argparse.Namespace) -> int:
    store, _cfg = _token_store(args)
    n = store.revoke(args.target)
    print(f"revoked {n} token(s)")
    return 0 if n else 1


def cmd_tokens_list(args: argparse.Namespace) -> int:
    import time as _t
    store, _cfg = _token_store(args)
    rows = store.list()
    if not rows:
        print("no tokens issued")
        return 0
    for r in rows:
        state = "REVOKED" if r["revoked"] else "active"
        age = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(r["issued_at"]))
        print(f"{state:8} {age}  {r['label'] or '(unlabeled)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="browserstonks",
        description="Agentic-browser CLI: form filling, browser session "
                    "tracking, and agent-harness chat for Chromium clients",
    )
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--root", default=str(ROOT),
                    help="config directory (default: CLI install dir)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="verbose logging (INFO+ pipeline steps)")
    ap.add_argument("--debug", action="store_true",
                    help="debug logging (payloads, internals)")

    # logging must be configured before any command runs
    import argparse as _ap
    pre = _ap.ArgumentParser(add_help=False)
    pre.add_argument("-v", "--verbose", action="store_true")
    pre.add_argument("--debug", action="store_true")
    pre_args, _ = pre.parse_known_args(argv)
    setup_logging(verbose=pre_args.verbose, debug=pre_args.debug)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="write config files")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("serve", help="start enabled integrations")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("plan", help="one-shot plan from a DOM snapshot")
    p.add_argument("dom", help="path to JSON file with the DOM state")
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("doctor", help="validate config/env/mcp")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("tokens", help="manage connection tokens "
                         "(auth.required mode)")
    tsub = p.add_subparsers(dest="tokens_cmd", required=True)
    tp = tsub.add_parser("issue", help="issue a new token")
    tp.add_argument("--label", default="", help="token label (e.g. 'work-laptop')")
    tp.set_defaults(fn=cmd_tokens_issue)
    tp = tsub.add_parser("revoke", help="revoke by token or label")
    tp.add_argument("target", help="plaintext token or label to revoke")
    tp.set_defaults(fn=cmd_tokens_revoke)
    tp = tsub.add_parser("list", help="list tokens (hashes, not plaintext)")
    tp.set_defaults(fn=cmd_tokens_list)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
