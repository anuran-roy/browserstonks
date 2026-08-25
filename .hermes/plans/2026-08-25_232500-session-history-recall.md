# Session History Recall — Implementation Plan (v3, client-packed history)

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Continuing a chat (panel restore, CLI restart, harness switch
mid-chat) gives the harness the prior conversation — packed by the panel
from its own transcript and injected by the CLI only when the harness-side
session is cold.

**Architecture:** The sidepanel sends a capped `history` array
(`[{role, content}, ...]`, last ~10 exchanges, ≤ ~8k chars) with every
`CHAT` message. The CLI injects it as a `PRIOR CONVERSATION` block into the
shared prompt ONLY when the harness session is fresh (first turn of a new
in-memory session) — subsequent turns rely on the harness's own memory.
Because the history comes from the client, switching harnesses mid-chat
keeps full continuity.

**Tech Stack:** sidepanel React (chrome-extension/sidepanel-src/app.jsx),
background relay (background.js), CLI (chat.py, prompt.py).

---

## Current context / assumptions

- The sidepanel holds the full restored transcript in `messages` state
  (restored via SESSION_LOAD from `cli/sessions/*.json`).
- The CLI persists transcripts in `cli/sessions/` (unchanged — still the
  authoritative record).
- HarnessManager keeps in-memory `sessions[harness][session_id]`; cold ==
  `session_id not in live`.
- Prompt assembly lives in `prompt.py::build_prompt`.

## Step-by-step plan

### Task 1: panel packs and sends history

**Files:** `chrome-extension/sidepanel-src/app.jsx`

In `send()`:

```js
const history = messages
  .filter((m) => m.role === "user" || m.role === "assistant")
  .slice(-20)                                   // last ~10 exchanges
  .map((m) => ({ role: m.role,
                 content: (m.content || "").slice(0, 2000) }));
// cap the whole block ~8000 chars, dropping oldest first
let packed = [], size = 0;
for (const m of [...history].reverse()) {
  size += m.content.length;
  if (size > 8000) break;
  packed.unshift(m);
}
// send: { type: "CHAT", harness, sessionId, message: text, mode, history: packed }
```

**Test:** rebuild sidepanel; verify the CHAT payload includes history after
a restored session, and stays small (<9KB) on long sessions.
**Commit:** `feat: panel packs capped chat history into CHAT messages`

### Task 2: background relays history

**Files:** `chrome-extension/background.js`

`CHAT` handler adds `history: msg.history || []` to the outgoing `chat` ws
message.

**Test:** node --check; log line shows the field present.
**Commit:** `feat: relay packed history to the CLI`

### Task 3: CLI renders the history block

**Files:** `cli/browserstonks/chat.py`, `cli/browserstonks/prompt.py`

- `chat.py::_handle_inner`:

```python
live = self.harnesses.sessions.setdefault(harness, {})
is_fresh = session_id not in live
history = None
if is_fresh:
    packed = msg.get("history") or []
    if packed:
        lines = [f"{'User' if m.get('role') == 'user' else 'Assistant'}: "
                 f"{(m.get('content') or '').strip()}"
                 for m in packed if (m.get('content') or '').strip()]
        if lines:
            history = ("PRIOR CONVERSATION (already happened — do not "
                       "repeat):\n" + "\n".join(lines))
context = {**self._context(), "_history": history}
```

- `prompt.py::build_prompt(..., history=None)` inserts the block before the
  browser-state JSON; `_shared_prompt` forwards `context["_history"]`.

**Test:** stub harness — first turn with history shows the block; second
turn (same live session) does not; empty history injects nothing.
**Commit:** `feat: CLI injects client-packed history on cold harness sessions`

### Task 4: regression + docs

- Fresh chat with no history behaves exactly as today.
- cli/README.md (Sessions & persistence: panel-packed recall, harness-switch
  continuity), root README.md changelog.

## Tests / validation

- Unit: prompt section placement; packing caps.
- Integration: two-turn stub-harness flow (inject exactly once).
- Live: restore a saved session, switch the harness dropdown to a different
  harness, ask "what were we discussing?" — the new harness answers from the
  packed history.

## Risks, tradeoffs, open questions

- **Payload overhead**: ~8k chars per message while a session is warm is
  redundant (CLI ignores it after the first turn). Acceptable; could be
  optimized later with a `hasLiveSession` flag handshake — deliberately
  deferred (YAGNI).
- **Trust**: client-supplied history — same trust level as the message
  itself in our single-operator model; the CLI store remains the
  authoritative record.
- **What gets packed**: user + assistant text only (no action blobs, no
  reasoning) — keeps it compact and readable for the model.
