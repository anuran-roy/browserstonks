# Naming Decision: Is the CLI a "Harness"?

**Question:** Can the BrowserStonks CLI be called a harness?

**Answer: No — and the distinction matters.** Keep "harness" for what it
already means in the codebase; the CLI is the **agent host / orchestrator**.

---

## Why not

In our architecture (and the code's own vocabulary), a **harness** is
specifically a *model-execution backend* that runs an agent loop: OpenCode,
OpenClaw, Hermes. The contract is `BaseHarness.send()` — give it a prompt,
it runs a model, returns `{reply, actions, meta}`. Harnesses are pluggable
(`HarnessManager.registry`), selectable from the panel dropdown, and
replaceable mid-chat.

The CLI does something categorically different — it **owns the browser
agency**:

- Session tracking (tabs, windows, focus), the action vocabulary and its
  enforcement (plan/build mode filtering), doc creation in `media/`,
  tab.read/tab.process/tab.edit execution, storage upload, token auth,
  transcript persistence, the websocket protocol with the extension.
- The harness *doesn't* act on the browser — it only emits `[[action]]`
  lines. The CLI is the one that actually **executes** them through the
  extension. Agency lives in the CLI.

Calling the CLI a harness would collapse the very distinction the
architecture is built on: many harnesses, one orchestrator. It would also
make docs like "harnesses.list" (which lists OpenCode/OpenClaw/Hermes)
confusing.

## What to call it instead

| Thing | Term |
|---|---|
| The CLI (`browserstonks serve` process) | **agent host** / **orchestrator** (the agentic-browser server) |
| OpenCode / OpenClaw / Hermes | **harness** (model-execution backend) — keep as-is |
| The Chrome extension | **client** / **browser bridge** — keep as-is |
| The whole system | agentic browser (as README already says) |

Note: "harness" in the wider agent world (e.g. "agent harness", LM
eval-harness, Claude Code as a "harness") often DOES mean the orchestrator
layer — so the term isn't wrong in the abstract, it's just already taken in
this repo by a narrower, well-defined meaning. Renaming now would break the
config schema (`harnesses.*`), the wire protocol (`harnesses.list`), the
panel dropdown, and every doc — for zero functional gain.

## Recommendation

Keep the vocabulary as-is. If anything, use "agent host" in prose when
describing the CLI's role in new docs. No code changes.
