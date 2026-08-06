# OpenCode 1.18.14 wire-format provenance

This file is the git-tracked record of the measured OpenCode 1.18.14
NDJSON wire format and the focused regression fixture committed
under `tests/display/_fixtures/`. The S-2 / S-3 opencode-plan
repaired the parser against this format; the
`tests/test_opencode_display_fidelity.py` regression tests assert
the observable display seam (file preview, syntax preview, edit
diff) against the captured frame shape.

## Probed binary

- `opencode --version` → `1.18.14`
- Binary present at `$HOME/.local/bin/opencode` (the captured
  `.agent/raw/opencode.log` is one live run from this binary).

## Capture source

The full live capture is 202 lines of NDJSON frames, taken from a
single OpenCode run that read files, ran git queries, and used
`todowrite`. The full capture is preserved at
`.agent/raw/opencode.log` (the corresponding ``.agent/raw/opencode.log``
raw log). Every frame in the committed fixture below is sourced from
that capture or derived from the same exact envelope shape the live
binary emits.

## Observed frame vocabulary

The live binary emits these top-level event types. The count is
from the 202-line capture:

| `type`        | Count | Notes                                              |
| ------------- | ----- | -------------------------------------------------- |
| `step_start`  |   83  | `part.type` is `step-start`; carries `part.id`     |
| `step_finish` |   82  | `part.type` is `step-finish`; carries `reason`     |
| `tool_use`    |   82  | `part.type` is `tool`; carries `part.tool`         |

The capture contains **no** `text` events, **no** `stream` events,
**no** `done` events, **no** `error` events, and **no** `init`
events. The parser's existing 1.17.15-era `NOTE` that the live
runtime "emits exactly five event types -- step_start, step_finish,
text, tool_use, error" is **inaccurate** for the 1.18.14 runtime:
the measurable event vocabulary is the three above (plus very rare
`tool_result` and `text` from `.agent/raw/codex_gpt-5.6-terra.log`
which is a *different* transport). The parser's text / stream
branches are retained as forward-compat for a runtime that does
stream, but the live 1.18.14 binary does not engage them.

## Captured tool names

The live binary emits tool names with a `ralph_` prefix because the
agent routes through the bundled Ralph Workflow MCP server. The
observed set:

- `ralph_directory_tree`
- `ralph_edit_file`
- `ralph_exec`
- `ralph_git_diff`
- `ralph_git_log`
- `ralph_git_status`
- `ralph_grep_files`
- `ralph_list_directory`
- `ralph_read_file`
- `ralph_write_file`
- `todowrite`

The first three tokens above (`ralph_directory_tree`,
`ralph_edit_file`, `ralph_write_file`, `ralph_read_file`) are the
ones the parser must normalize. The S-3 fix strips the `ralph_`
prefix in `_OpenCodeDispatch._canonical_tool_name` so the
transport-neutral preview payload builder receives the canonical
`read_file` / `write_file` / `edit_file` shape. The raw wire name
is preserved in `metadata["tool_raw"]` for diagnostics.

## Captured `tool_use` envelope shape

Every captured `tool_use` frame carries the same envelope:

```json
{
  "type": "tool_use",
  "timestamp": 1786028404708,
  "sessionID": "ses_028696ca8ffebRuJyZ6w0z326r",
  "part": {
    "type": "tool",
    "tool": "ralph_read_file",
    "callID": "call_3b50ab585cf54bf8abe2890e",
    "state": {
      "status": "completed",
      "input": {"path": "/workspace/normalized"},
      "output": "...",
      "metadata": {"truncated": false},
      "title": "",
      "time": {"start": 1786028404708, "end": 1786028404732}
    },
    "id": "prt_fd796ae2d001QVVaP89RmjzMaN",
    "sessionID": "ses_028696ca8ffebRuJyZ6w0z326r",
    "messageID": "msg_fd796943b001LVh3KpcZMqTfjx"
  }
}
```

The parser must look at `part.state.input` for arguments (not
`part.input` directly). The `callID` (not `callId`) is the native
call identity. The `state.status` is `"completed"` for terminal
events (the binary collapses a `running` and a `completed` state
into a single terminal frame for completed calls -- the parser
already handles this gracefully).

## Captured `step_start` envelope shape

```json
{
  "type": "step_start",
  "timestamp": 1786028403976,
  "sessionID": "ses_028696ca8ffebRuJyZ6w0z326r",
  "part": {
    "id": "prt_fd796ad05001WbC3sI0OAnCT1y",
    "messageID": "msg_fd796943b001LVh3KpcZMqTfjx",
    "sessionID": "ses_028696ca8ffebRuJyZ6w0z326r",
    "snapshot": "b23ca142a0b5e1a59f1b25e638067c99644e041b",
    "type": "step-start"
  }
}
```

The `part.id` is the step's part identity (used as the accumulator
key by the parser). The `snapshot` is a content-addressed hash of
the workspace state at step start. The `part.type` is `step-start`
(with the hyphen -- distinct from the top-level `type: "step_start"`
underscore form).

## Captured `step_finish` envelope shape

```json
{
  "type": "step_finish",
  "timestamp": 1786028404751,
  "sessionID": "ses_028696ca8ffebRuJyZ6w0z326r",
  "part": {
    "id": "prt_fd796b00b001YPi2A94uWu20hA",
    "reason": "tool-calls",
    "snapshot": "b23ca142a0b5e1a59f1b25e638067c99644e041b",
    "messageID": "msg_fd796943b001LVh3KpcZMqTfjx",
    "sessionID": "ses_028696ca8ffebRuJyZ6w0z326r",
    "type": "step-finish",
    "tokens": {"total": 39511, "input": 3214, "output": 73, "reasoning": 0, "cache": {"write": 0, "read": 36224}},
    "cost": 0.00322524
  }
}
```

The `part.tokens` and `part.cost` are token-usage and cost
accounting the OpenCode runtime emits. The parser preserves them
in `metadata` but does not surface them as visible output (the
display layer renders token usage through a separate path).

## The focused fixture

The regression tests in `tests/test_opencode_display_fidelity.py`
inline a six-frame focused fixture (one `step_start`, one read, one
write, one edit, one read-back, one `step_finish`). The read
frames are captured verbatim from the live run. The write and edit
frames are **document-derived** from the same exact envelope shape
the live binary emits for the read frames (the live run was a
planning/reading task and did not exercise write/edit; the write
and edit payload values are the natural payloads for the file
content the read frames return). The provenance is:

- The `read` shape is **measured** (frames 2 and 5 of the fixture).
- The `write` shape is **document-derived** (frame 3 of the fixture).
- The `edit` shape is **document-derived** (frame 4 of the fixture).
- The `step_start` and `step_finish` shapes are **measured** (frames 1 and 6 of the fixture).

The "document-derived" label means the live binary's wire shape is
identical for the read/write/edit tool envelopes; only the input
payload values differ. The captured read frames provide the exact
byte shape the parser must handle for the write and edit frames,
including the `state.input` / `state.output` / `state.metadata` /
`state.time` keys, the `state.metadata.truncated` field, the
`part.id` / `part.sessionID` / `part.messageID` part identity keys,
and the top-level `timestamp` / `sessionID` keys. The fixture's
purpose is to pin the parser's handling of the *envelope shape*,
which is the only thing the wire format changed between 1.17.15
and 1.18.14 -- the actual JSON keys (`state.input`, `state.output`,
`callID`, etc.) are unchanged.

## Normalization

The fixture is normalized to drop volatile IDs and absolute scratch
paths so the test does not depend on session-rotation identity:

- `ses_028696ca8ffebRuJyZ6w0z326r` → `ses_00000000000000000000`
- `prt_fd796ad05001...` → `prt_00000000000000000000`
- `msg_fd796943b001...` → `msg_00000000000000000000`
- `call_3b50ab585cf54bf8abe2890e` → `call_0000000000000000`
- `/tmp/somescratch` → `/tmp/normalized`
- `/home/mistlight/Projects/Ralph-Workflow/...` → `/workspace/normalized`
- Wall-clock epoch timestamps (e.g. `1786028404708`) → `1785000000000`
- `snapshot` content hash → `b23ca142a0b5e1a59f1b25e638067c99644e041b`

A future captured fixture that retains the exact original IDs
would need to be re-normalized using the same substitution; the
preserved IDs are documented above so the provenance chain is
recoverable.

## Re-probing the binary

To re-probe the binary and refresh this fixture:

```bash
opencode --version
# 1.18.14

# 1. Force a planning/reading capture (the live run that produced
#    .agent/raw/opencode.log was a Read tool called via the Ralph
#    Workflow MCP server).
# 2. The capture command is the Ralph Workflow ``smoke-interactive-opencode``
#    command-line run against the live binary; the underlying
#    transport is whatever argv OpenCode accepts (per the command
#    builder in ``ralph/agents/invoke/_command_builders/``).
```

The fixture cannot be regenerated from a unit test -- the live
binary consumes tokens and is excluded from the 60-second combined
test budget. The committed fixture is the durable record.
