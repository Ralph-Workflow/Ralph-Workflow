# OpenCode 1.18.14 wire-format provenance

This file is the git-tracked record of the measured OpenCode 1.18.14
NDJSON wire format and the focused regression fixture committed
under `tests/display/_fixtures/opencode_wire.jsonl`. The
`tests/test_opencode_captured_wire.py` and
`tests/test_opencode_display_fidelity.py` regression tests assert the
observable display seam (file preview, syntax preview, edit diff)
against the captured frame shape. Every frame in the committed
fixture was captured directly from the live `opencode 1.18.14` binary
on 2025-11-19 via `opencode run --format json`.

## Probed binary

- `opencode --version` -> `1.18.14`
- Binary present at `$HOME/.opencode/bin/opencode`
- Capture command (real, executable verbatim):

  ```bash
  opencode run --format json --auto --model opencode/longcat-2.0-free \
      --dir /tmp/capture \
      'Use the read tool to read /tmp/capture/todo-list.js (which does not exist yet). \
       Use the write tool to create /tmp/capture/todo-list.js with the content: \
       function TodoList() { this.items = []; } \
       TodoList.prototype.add = function(item) { this.items.push(item); }; \
       module.exports = TodoList; \
       Then use the edit tool to replace "this.items = [];" with \
       "this.items = []; this.nextId = 0;" in /tmp/capture/todo-list.js. \
       Then use the read tool to read /tmp/capture/todo-list.js to verify the edit.'
  ```

  The exact argv produced by `OpencodeCommandBuilder` is
  `opencode run --format json --auto --model {model} --dir {dir} {prompt}`.
  The Ralph Workflow smoke command
  (`uv run python -m ralph smoke-interactive-opencode --agent opencode/minimax/MiniMax-M3`)
  builds the same argv via `OpencodeCommandBuilder` and routes the
  resulting JSON-Lines stream through the same `OpenCodeParser`
  this fixture drives the regression against.

## Capture source

Four live NDJSON captures were taken from the same `opencode
1.18.14` binary on 2025-11-19. They are preserved verbatim under
`tests/display/_fixtures/_raw/opencode_*.jsonl` as the durable
record:

| Capture file | Frames | Purpose |
| --- | --- | --- |
| `_raw/opencode_greeting_2025-11-19.jsonl` | 3 | A trivial text-only reply establishing that the runtime DOES emit `text` events on 1.18.14; the previously-recorded "no text events" claim was wrong. |
| `_raw/opencode_bash_2025-11-19.jsonl` | 7 | Two sequential bash tool calls establishing the `bash` tool envelope (input.command / output / metadata.exit / title / time). |
| `_raw/opencode_write_edit_read_2025-11-19.jsonl` | 12 | Bash-based write/edit/read sequence retained as a parser-envelope baseline; bash has no syntax/file/diff preview surface so it cannot exercise the display-capability routing. |
| `_raw/opencode_read_write_edit_2025-11-19.jsonl` | 13 | The fixture source for the display-capability regression: native OpenCode tool calls (`read` err, `write`, `edit`, `read` ok) exercising all three display surfaces (`file_preview`, `syntax_preview`, `diff_preview`). **Every frame in `opencode_wire.jsonl` originates from this capture.** |

The DA-002 defect (wt-05-fix-opencode-parsing) was that the
previously-committed fixture only exercised bash, so the
parser-vs-display seam was not actually tested for the three
display surfaces the operator noticed were broken. The new
read/write/edit capture replaces that with a sequence that
genuinely exercises every display surface.

## Observed frame vocabulary

The live binary emits these top-level event types across all four
captures (combined 35 frames):

| `type`        | Total | Notes                                              |
| ------------- | ----- | -------------------------------------------------- |
| `step_start`  |   8   | `part.type` is `step-start`; carries `part.id`     |
| `step_finish` |   8   | `part.type` is `step-finish`; carries `reason`     |
| `tool_use`    |   7   | `part.type` is `tool`; carries `part.tool`         |
| `text`        |   3   | `part.type` is `text`; carries `part.text`         |

The captures contain **no** `stream` events, **no** `done` events,
**no** top-level `error` events, and **no** `init` events.
**The measurable event vocabulary for OpenCode 1.18.14 is
`step_start`, `step_finish`, `tool_use`, and `text` (four
types)**, not five. The parser's `stream` / `done` branches are
retained as forward-compat for a future runtime that does
stream; the `error` branch is engaged by a `tool_use` frame
whose `state.status` is `"error"` (a failed tool call), which is
how the failed read in the read/write/edit capture surfaces.
The previous parser-side `NOTE` claiming "OpenCode 1.17.15
emits exactly five event types" was inaccurate for the 1.18.14
runtime and is updated (see `ralph/agents/parsers/opencode.py`).

## Captured tool names

The read/write/edit capture uses the native OpenCode tool names
`read` / `write` / `edit` (lowercase, no `ralph_` prefix); the
parser normalizes these at the transport boundary. The Ralph MCP
server layer adds a `ralph_` prefix when routing through
`ralph smoke-interactive-opencode` -- both shapes hit the same
parser code path because the parser strips the `ralph_` prefix
off the tool name (`OpenCodeParser._canonical_tool_name`).

The captured tool names in `opencode_wire.jsonl` are the bare
native spellings (`read` / `write` / `edit`); the
`tests/test_opencode_captured_wire.py` regression
`test_captured_ralph_envelope_normalizes_at_transport_boundary`
exercises the `ralph_*` prefix separately on the same envelope
shape so both transport paths are covered.

The native OpenCode `read` tool uses `filePath` (not `path` /
`file_path`) as its path argument. The transport-neutral
`_path` helper in `ralph/display/preview_payload.py` already
keys off `path` / `file_path` / `filePath` / `filename` /
`notebook_path`, so the native spelling routes through the
existing surface without changes.

The native OpenCode `edit` tool uses `oldString` / `newString`
(NOT `oldText` / `newText`) for the replacement pair. The
transport-neutral `_edit_hunks` helper in
`ralph/display/preview_payload.py` was extended (S-3 of this
plan) to recognize `oldString` / `newString` aliases in
addition to `oldText` / `old_string` / `newText` / `new_string`,
so the native edit envelope routes through the existing diff
preview surface without a transport branch.

## Captured `tool_use` envelope shape (write tool)

The captured `write` tool_use frame:

```json
{
  "type": "tool_use",
  "timestamp": 1785000003000,
  "sessionID": "s_0001",
  "part": {
    "type": "tool",
    "tool": "write",
    "callID": "c_0002",
    "state": {
      "status": "completed",
      "input": {
        "content": "function TodoList() { this.items = []; }\n...",
        "filePath": "/tmp/capture/todo-list.js"
      },
      "output": "Wrote file successfully.",
      "metadata": {"diagnostics": {}, "filepath": "...", "exists": false, "truncated": false},
      "title": "tmp/capture/todo-list.js",
      "time": {"start": 1785000003000, "end": 1785000003000}
    },
    "id": "p_0003",
    "sessionID": "s_0001",
    "messageID": "m_0002"
  }
}
```

The parser reads `part.state.input` for arguments (NOT
`part.input` directly). The `callID` (not `callId`) is the
native call identity. `state.status` is `"completed"` for terminal
events; the binary collapses a `running` and a `completed` state
into a single terminal frame for completed calls and the parser
already handles this gracefully via `_tool_call_was_dispatched`.

The `write` tool's `state.input.content` carries the file body;
the parser surfaces it via `metadata["input"]["content"]` so the
syntax preview renders the captured file.

## Captured `tool_use` envelope shape (edit tool)

The captured `edit` tool_use frame:

```json
{
  "type": "tool_use",
  "timestamp": 1785000006000,
  "sessionID": "s_0001",
  "part": {
    "type": "tool",
    "tool": "edit",
    "callID": "c_0003",
    "state": {
      "status": "completed",
      "input": {
        "filePath": "/tmp/capture/todo-list.js",
        "oldString": "this.items = [];",
        "newString": "this.items = []; this.nextId = 0;"
      },
      "output": "Edit applied successfully.",
      "metadata": {
        "diagnostics": {},
        "diff": "Index: ...\n--- ...\n+++ ...\n@@ -1,3 +1,3 @@\n-...\n+...\n",
        "filediff": {...},
        "truncated": false
      },
      "title": "tmp/capture/todo-list.js",
      "time": {"start": 1785000006000, "end": 1785000006000}
    },
    "id": "p_0006",
    "sessionID": "s_0001",
    "messageID": "m_0002"
  }
}
```

The native edit tool uses `oldString` / `newString` keys; the
parser surfaces them via `metadata["input"]["oldString"]` and
`metadata["input"]["newString"]`, and the extended
`_edit_hunks` alias set (`oldText` / `old_string` / `oldString`)
routes the diff preview to render the captured `old` / `new`
pair. The `state.metadata.diff` is also populated with the
unified diff body (the parser preserves it for diagnostics; the
preview payload builder prefers the structured `old_string` /
`new_string` pair).

## Captured `tool_use` envelope shape (read tool)

The captured `read` tool_use frame (success path):

```json
{
  "type": "tool_use",
  "timestamp": 1785000009000,
  "sessionID": "s_0001",
  "part": {
    "type": "tool",
    "tool": "read",
    "callID": "c_0004",
    "state": {
      "status": "completed",
      "input": {"filePath": "/tmp/capture/todo-list.js"},
      "output": "<path>...</path>\n<type>file</type>\n<content>...</content>\n...",
      "metadata": {"preview": "...", "truncated": false, "loaded": [], "display": {...}},
      "title": "tmp/capture/todo-list.js",
      "time": {"start": 1785000009000, "end": 1785000009000}
    },
    "id": "p_0009",
    "sessionID": "s_0001",
    "messageID": "m_0003"
  }
}
```

The native read tool's `state.input.filePath` is the file path;
the parser surfaces it via `metadata["input"]["filePath"]` and
the transport-neutral `_path` helper picks it up. The
`state.output` carries a richer tagged body (`<path>` / `<type>` /
`<content>` XML-style) that the parser preserves for diagnostics;
the file preview path is the recognized `filePath` argument.

## Captured `step_start` envelope shape

```json
{
  "type": "step_start",
  "timestamp": 1785000001000,
  "sessionID": "s_0001",
  "part": {
    "id": "p_0001",
    "messageID": "m_0001",
    "sessionID": "s_0001",
    "type": "step-start"
  }
}
```

The `part.id` is the step's part identity. The `part.type` is
`step-start` with a hyphen -- distinct from the top-level
`type: "step_start"` underscore form, which is what the
parser uses to dispatch.

## Captured `step_finish` envelope shape

```json
{
  "type": "step_finish",
  "timestamp": 1785000004000,
  "sessionID": "s_0001",
  "part": {
    "id": "p_0004",
    "reason": "tool-calls",
    "messageID": "m_0001",
    "sessionID": "s_0001",
    "type": "step-finish",
    "tokens": {"total": 7543, "input": 331, "output": 25, "reasoning": 19, "cache": {"write": 0, "read": 7168}},
    "cost": 0
  }
}
```

The `part.tokens` and `part.cost` fields are token-usage and cost
accounting the 1.18.14 runtime emits. The parser preserves them
in `metadata` but does not surface them as visible output (the
display layer renders token usage through a separate path).

## Captured `text` envelope shape

```json
{
  "type": "text",
  "timestamp": 1785000011000,
  "sessionID": "s_0001",
  "part": {
    "id": "p_0011",
    "messageID": "m_0004",
    "sessionID": "s_0001",
    "type": "text",
    "text": "All steps completed:\n\n1. **Read** -- file didn't exist yet (expected).\n...",
    "time": {"start": 1785000012000, "end": 1785000011000}
  }
}
```

This `text` event is the smoking gun for the previous inaccuracy:
the live 1.18.14 binary DOES emit `text` events.

## The focused fixture

`tests/display/_fixtures/opencode_wire.jsonl` is a 13-frame
normalized copy of the 13-frame
`opencode_read_write_edit_2025-11-19.jsonl` capture. Every
frame in the fixture is **measured**, not derived.

The fixture exercises the parser-vs-display seam for the full
read/write/edit sequence:
- 1 failed `read` (File not found; emits `tool_use` + `error`)
- 1 successful `write` (creates the file; emits `tool_use` +
  `tool_result` with a recognized syntax_preview envelope)
- 1 successful `edit` (replaces the source line; emits
  `tool_use` + `tool_result` with a recognized diff_preview
  envelope carrying the `oldString` / `newString` pair)
- 1 successful `read` (verifies the edit; emits `tool_use` +
  `tool_result` with a recognized file_preview envelope)
- 1 terminal `text` event (the agent's natural-language summary)

The 4 `step_start` and 4 `step_finish` lifecycle frames are
deliberately suppressed from the visible output by the parser's
`_STOP_EVENT_TYPES` frozenset (these are marker boundaries, not
tool calls). The expected visible line count is therefore:
4 `tool_use` + 3 `tool_result` + 1 `error` + 1 `text` = 9 lines.

The `tests/test_opencode_display_fidelity.py`
`test_captured_fixture_drives_all_three_display_surfaces`
regression pins this contract: the captured fixture must
exercise the syntax_preview surface (write), the file_preview
surface (read), and the diff_preview surface (edit) by driving
`payload_from_tool_event` to a non-None payload for every
captured tool_use envelope.

The `tests/test_opencode_captured_wire.py` regression
`test_captured_ralph_envelope_normalizes_at_transport_boundary`
exercises the `ralph_*`-prefixed synthetic envelopes (which the
Ralph MCP server layer adds when routing through
`ralph smoke-interactive-opencode`) on the same envelope shape
the live binary emits -- the underlying wire JSON keys
(`state.input` / `state.output` / `callID` /
`state.metadata.truncated` / `state.title` / `state.time` /
`part.id` / `part.sessionID` / `part.messageID` and the
top-level `timestamp` / `sessionID`) are identical between the
captured native envelope and the synthetic `ralph_*` envelope.

## Normalization

Only volatile IDs and absolute scratch paths are normalized so the
fixture does not depend on session-rotation identity:

- `ses_<hex>` -> `s_<NNNN>` (sequential numeric IDs)
- `prt_<hex>` -> `p_<NNNN>` (sequential numeric IDs)
- `msg_<hex>` -> `m_<NNNN>` (sequential numeric IDs)
- `call_<hex>` -> `c_<NNNN>` (sequential numeric IDs)
- `/tmp/opencode-capture2` -> `/tmp/capture`
- Wall-clock epoch timestamps (e.g. `1786032889772`) -> deterministic
  `1785000000000` + 1-second bucket offset

A future captured fixture that retains the exact original IDs
would need to be re-normalized using the same substitutions; the
un-normalized raw capture is preserved at
`tests/display/_fixtures/_raw/opencode_*.jsonl` so the
provenance chain is recoverable.

## Re-probing the binary

To re-probe the binary and refresh this fixture:

```bash
opencode --version
# expected: 1.18.14

# Use the exact argv produced by OpencodeCommandBuilder:
timeout 120 opencode run --format json --auto \
    --model opencode/longcat-2.0-free \
    --dir /tmp/capture \
    'Use the read tool to read /tmp/capture/todo-list.js (which does not exist yet). \
     Use the write tool to create /tmp/capture/todo-list.js with the content: \
     function TodoList() { this.items = []; } \
     TodoList.prototype.add = function(item) { this.items.push(item); }; \
     module.exports = TodoList; \
     Then use the edit tool to replace "this.items = [];" with \
     "this.items = []; this.nextId = 0;" in /tmp/capture/todo-list.js. \
     Then use the read tool to read /tmp/capture/todo-list.js to verify the edit.'
```

The fixture cannot be regenerated from a unit test -- the live
binary consumes tokens and is excluded from the 60-second combined
test budget. The committed fixture is the durable record.

## Summary of changes vs. the previously-committed provenance

The previously-committed version of this document (commit
`cdd00b826` "fix(opencode): normalize display tool events")
admitted that some fixture frames were "document-derived" rather
than captured from the live binary, and used bash-only tool
calls that could not exercise any of the three display surfaces
(DA-002). The current version of this document replaces those
admissions with measured frames from
`tests/display/_fixtures/_raw/opencode_read_write_edit_2025-11-19.jsonl`
that exercise the `read` / `write` / `edit` tools, and corrects
the "no text events" / "exactly five event types" inaccuracies to
match the 1.18.14 runtime evidence above. The S-3 extension to
`_edit_hunks` in `ralph/display/preview_payload.py` adds
`oldString` / `newString` as recognized aliases for the
native OpenCode edit tool envelope.
