"""Shared system prompt describing the BrowserStonks action vocabulary.

Every harness gets this prepended to its turn so it knows exactly which
events it can emit and their argument shapes. Keep the format rows exact
— `_extract_actions` parses them.

Two axes shape the vocabulary:
  MODE            plan (read-only) | build (docs + page actions)
  INTEGRATION     the type of client connected to the CLI:
                  browser:full   — a real browser the user is driving
                                   (Chrome extension today). Full vocabulary.
                  browser:headless — Selenium/Playwright-style automation.
                                   Same browser vocabulary; no user watching.
                  shell          — a terminal shell (bash/zsh/fish/pwsh).
                                   No tabs; file/shell-ish actions only.
                  tool           — an AI tool integrating via tool-calls.
                                   Read-only window onto the session state.
                  harness        — another harness calling the CLI.
                                  Read-only; it plans, the CLI acts.

The integration type arrives with the websocket connection
({"integrationType": "browser:full"}) and rides the chat context.
"""
from __future__ import annotations

_MODES = {
    "plan": {
        "actions": ["tab.new", "tab.open", "tab.close", "tab.read",
                    "tab.process", "tab.focus"],
        "doc": False,
    },
    "build": {
        "actions": ["tab.new", "tab.open", "tab.close", "tab.read",
                    "tab.process", "tab.focus", "tab.action",
                    "tab.edit", "tab.submit",
                    "doc.create", "doc.modify", "doc.delete"],
        "doc": True,
    },
}

# Integration types -> action ops the connected client can execute.
# Anything not listed for an integration type is filtered before execution.
_INTEGRATION_OPS = {
    "browser:full": None,      # None = full mode vocabulary (no filter)
    "browser:headless": None,  # same browser verbs, headless driver
    "shell": {"doc.create", "doc.modify", "doc.delete"},
    "tool": {"tab.read", "tab.process"},
    "harness": {"tab.read", "tab.process", "doc.create", "doc.modify",
                "doc.delete"},
}

_INTEGRATION_DESC = {
    "browser:full": (
        "the user's real browser (extension). Pages carry their login "
        "state; tab ids come from the session state below; the user "
        "watches tabs open and close"),
    "browser:headless": (
        "an automated headless browser (Selenium-style). Same actions, "
        "but no user is watching — batch/research style usage"),
    "shell": (
        "a terminal shell. There are NO tabs and NO browser: only the "
        "doc.* actions are available; everything else must be answered "
        "from the conversation"),
    "tool": (
        "an AI tool integrating via tool-calls. You may read and process "
        "tabs only — you cannot open, close, or edit anything"),
    "harness": (
        "another agent harness calling this CLI. You may read/process "
        "tabs and create/modify/delete docs, but not drive the user's "
        "browser directly"),
}

_BROWSER_INTEGRATIONS = {"browser:full", "browser:headless"}


def _action_lines(mode: str, integration: str = "browser:full") -> str:
    """Per-mode, per-integration action vocabulary block."""
    browser = integration in _BROWSER_INTEGRATIONS
    if not browser:
        # shell / tool / harness: docs-only or read-only subsets
        if integration == "shell":
            return """  [[doc.create|<format>|<filename>]] create a document; format is html, markdown, or text.
                                     Put the document body in a fenced code block
                                     (```html / ```markdown / ```text) in the same reply.
  [[doc.modify|<filename>|<instruction>]]  modify an existing doc in this session's
                                     media folder (media/<session>/). Include a fenced
                                     block with the NEW full content.
  [[doc.delete|<filename>]]        delete a doc from this session's media folder.
  NOTE: the connected client is a SHELL — no browser actions are available."""
        subset = """  [[tab.read|<tabIdOrUrl>]]         read a tab's HTML: numeric tab id, or a URL substring.
                                     The HTML is returned to you in the next turn.
  [[tab.process|<tabIdOrUrl>]]      run your own Python code against a tab's HTML.
                                     Include a fenced ```python block defining:
                                         def process(HTML):
                                             ...          # HTML is the page source (str)
                                             return {...}  # JSON-serializable result
                                     Your code runs CLI-side; stdlib only.
                                     The return value comes back to you in the next turn."""
        if integration == "harness":
            subset += """
  [[doc.create|<format>|<filename>]] create a document (body in a fenced block).
  [[doc.modify|<filename>|<instruction>]]  modify an existing doc (new full body in a fenced block).
  [[doc.delete|<filename>]]        delete a doc from this session's media folder."""
        return subset
    # browser integrations: the full mode-based vocabulary
    base = """  [[tab.new|<url>]]                 open a new tab at the URL and focus it
  [[tab.open|<url>]]                alias of tab.new
  [[tab.close|<tabId>]]             close a tab by its numeric id (omit to close the active tab)
  [[tab.read|<tabIdOrUrl>]]         read a tab's HTML: numeric tab id, or a URL substring.
                                     The HTML is returned to you in the next turn.
  [[tab.process|<tabIdOrUrl>]]      run your own Python code against a tab's HTML.
                                     Include a fenced ```python block defining:
                                         def process(HTML):
                                             ...          # HTML is the page source (str)
                                             return {...}  # JSON-serializable result
                                     Your code runs CLI-side; stdlib only.
                                     The return value comes back to you in the next turn.
  [[tab.focus|<tabId>]]               bring an already-open tab to the front (no new tab).
  After browsing, clean up: close tabs you opened with [[tab.close|…]]."""
    if mode == "plan":
        return base
    # build mode
    return base + """
  [[tab.action|<tabIdOrUrl>|<op>|<args>]]  act on a page:
                                     op is fill(selector, value) / click(selector) /
                                     select(selector, value) / submit() — same ops as the
                                     form-filling pipeline. args is a JSON array, e.g.
                                     [[tab.action|42|fill|["#email","me@x.com"]]]
  [[tab.edit|<tabIdOrUrl>|<selector>|<spec>]]  edit the DOM of matching elements:
                                     spec is JSON — {"html": "..."} to replace innerHTML,
                                     {"text": "..."} to replace text, {"attrs": {...}} to
                                     set attributes, {"remove": true} to delete elements.
                                     Example: [[tab.edit|42|#msg|{"html":"<p>hi</p>"}]]
  [[tab.submit|<tabIdOrUrl>]]      submit the form closest to the last element you
                                     edited/filled (or the page's first submit button).
  [[doc.create|<format>|<filename>]] create a document; format is html, markdown, or text.
                                     Put the document body in a fenced code block
                                     (```html / ```markdown / ```text) in the same reply.
  [[doc.modify|<filename>|<instruction>]]  modify an existing doc in this session's
                                     media folder (media/<session>/). Include a fenced
                                     block with the NEW full content.
  [[doc.delete|<filename>]]        delete a doc from this session's media folder."""


HARNESS_SYSTEM_PROMPT_TEMPLATE = """You are the agent behind BrowserStonks, a system that turns the user's
browser into an agentic browser. You chat with the user and you
can ACT by emitting action lines in your reply.

MODE: {mode} ({mode_desc})
CONNECTED CLIENT: {integration} — {integration_desc}

ACTION LINES — put each on its own row, exactly in these formats:

{action_lines}

Rules for actions:
- CRITICAL: do NOT use your tools (bash, edit, etc.) for actions the
  integration can perform. Opening URLs, reading pages, browsing — that is
  what action lines are for. Never run curl/wget when a browser integration
  is connected; pages must be read through the user's browser (action
  lines), which has their sessions and login state.
- Only emit action lines that are ALLOWED in this mode ({allowed_ops}).
  Others are ignored.
- Only emit action lines when the user wants you to act (open, browse,
  close, read, process, create, modify, delete). Never emit them speculatively.
- Use real, complete URLs (include the https:// scheme).
- For tab.close / tab.read / tab.process / tab.focus / tab.action use the
  tab id from the browser session state below, or a distinctive URL substring.
- You may combine normal markdown explanation with action lines.
- WORK LOOP: you will receive tab.read / tab.process results as an ACTION
  RESULTS turn. Keep emitting further actions (read other pages, process
  HTML, open more tabs) until you have EVERYTHING you need for the task —
  do not stop halfway and wait for the user. Only give your final answer
  (no action lines) when the whole task is complete. Multi-step research
  across many reads in one conversation turn is expected and supported.
- If you emit tab.new/tab.open together with tab.read, the tab opens
  first and the read result comes back in the ACTION RESULTS turn —
  you can then read it again or continue with other pages.
- Doc body: write the content in the fenced block; the filename should
  end in the matching extension (.html / .md / .txt).

You can see the client's session state below (profiles, windows, open
tabs with ids, and which tab is active). Use tab ids from there when
closing tabs, and use the state to answer questions about what's open.
The tab the user is CURRENTLY looking at is called out explicitly
below the JSON — assume that is the page they refer to with "this
page" / "the current tab" unless they say otherwise. Before opening a
new tab, check the state: if the URL is already open, READ it instead
of opening a duplicate.

Session state (JSON):
"""

_MODE_DESC = {
    "plan": "read-only: browse/read/process/close/focus tabs; no document "
            "creation or page mutation",
    "build": "full: everything plan has, plus document creation/modification/"
             "deletion and page actions (fill/click/submit)",
}


def build_prompt(message: str, browser_state: dict | None = None,
                 active_tab: dict | None = None,
                 mode: str = "plan",
                 history: str | None = None,
                 integration: str = "browser:full") -> str:
    import json
    mode_spec = _MODES.get(mode, _MODES["plan"])
    integration = integration if integration in _INTEGRATION_OPS else "browser:full"
    prompt = (HARNESS_SYSTEM_PROMPT_TEMPLATE
              .replace("{mode}", mode)
              .replace("{mode_desc}", _MODE_DESC.get(mode, _MODE_DESC["plan"]))
              .replace("{integration}", integration)
              .replace("{integration_desc}",
                       _INTEGRATION_DESC.get(integration,
                                             _INTEGRATION_DESC["browser:full"]))
              .replace("{action_lines}", _action_lines(mode, integration))
              .replace("{allowed_ops}", ", ".join(mode_spec["actions"])))
    if history:
        prompt += f"\n{history}\n"
    prompt += json.dumps(browser_state or {}, indent=2)[:4000]
    if active_tab:
        prompt += (f"\n\nCURRENT TAB (the page the user is looking at now): "
                   f"id={active_tab.get('id')} "
                   f"url={active_tab.get('url')} "
                   f"title={active_tab.get('title')}")
    else:
        prompt += "\n\nCURRENT TAB: unknown (no active tab reported)"
    return prompt + f"\n\nUser: {message}"


def allowed_actions(mode: str, integration: str = "browser:full") -> set[str]:
    """Action ops allowed for a mode + integration type (used to filter/
    validate harness output)."""
    mode_ops = set(_MODES.get(mode, _MODES["plan"])["actions"])
    itype_ops = _INTEGRATION_OPS.get(integration)
    if itype_ops is None:
        return mode_ops  # browser integrations: no extra filter
    return mode_ops & set(itype_ops)
