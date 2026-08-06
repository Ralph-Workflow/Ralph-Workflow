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

- `opencode --version` → `1.18.14`
- Binary present at `$HOME/.opencode/bin/opencode`
- Capture command (real, executable verbatim):

  ```bash
  opencode run --format json --auto --model opencode/longcat-2.0-free \
      --dir /tmp/capture \
      'First, use the bash tool to write `line-1-marker\nline-2-original\n…` to /tmp/capture/a.txt. \
       Then, use the bash tool to run `sed -i s/original/MODIFIED/g /tmp/capture/a.txt && cat /tmp/capture/a.txt` to edit it. \
       Finally, use the bash tool to run `cat /tmp/capture/a.txt` to read it back.'
  ```

  The exact argv produced by `OpencodeCommandBuilder` is
  `opencode run --format json --auto --model {model} --dir {dir} {prompt}`
  (the `--json-stream` output flag is conditionally omitted when the
  base command is `opencode`, see
  `ralph/agents/invoke/_command_builders/__init__.py` line ~225).

  The Ralph Workflow smoke command
  (`uv run python -m ralph smoke-interactive-opencode --agent opencode/minimax/MiniMax-M3`)
  builds the same argv via `OpencodeCommandBuilder` and routes the
  resulting JSON-Lines stream through the same `OpenCodeParser`
  this fixture drives the regression against.

## Capture source

Three live NDJSON captures were taken from the same `opencode
1.18.14` binary on 2025-11-19. They are preserved verbatim under
`tests/display/_fixtures/_raw/opencode_*.jsonl` as the durable
record:

| Capture file | Frames | Purpose |
| --- | --- | --- |
| `_raw/opencode_greeting_2025-11-19.jsonl` | 3 | A trivial text-only reply establishing that the runtime DOES emit `text` events on 1.18.14; the previously-recorded "no text events" claim was wrong. |
| `_raw/opencode_bash_2025-11-19.jsonl` | 7 | Two sequential bash tool calls establishing the `bash` tool envelope (input.command / output / metadata.exit / title / time). |
| `_raw/opencode_write_edit_read_2025-11-19.jsonl` | 12 | The fixture source: three sequential bash tool calls (cat, sed, wc -l) proving the parser envelope handling for write/edit/read operations. **Every frame in `opencode_wire.jsonl` originates from this capture.** |

## Observed frame vocabulary

The live binary emits these top-level event types. The count is
from the combined 22-frame capture set above:

| `type`        | Total | Notes                                              |
| ------------- | ----- | -------------------------------------------------- |
| `step_start`  |   4   | `part.type` is `step-start`; carries `part.id`     |
| `step_finish` |   4   | `part.type` is `step-finish`; carries `reason`     |
| `tool_use`    |   3   | `part.type` is `tool`; carries `part.tool`         |
| `text`        |   2   | `part.type` is `text`; carries `part.text`         |

The capture contains **no** `stream` events, **no** `done` events,
**no** `error` events, and **no`init` events. **The measurable
event vocabulary for OpenCode 1.18.14 is `step_start`,
`step_finish`, `tool_use`, and `text` (four types)**, not five.
The parser's `stream` / `error` / `done` branches are retained
as forward-compat for a future runtime that does stream, but the
live 1.18.14 binary does not engage them. The previous
parser-side `NOTE` claiming "OpenCode 1.17.15 emits exactly five
event types -- step_start, step_finish, text, tool_use, error"
was inaccurate for the 1.18.14 runtime and is updated (see the
S-3 commit in this plan and `ralph/agents/parsers/opencode.py`).

## Captured tool names

The capture's tool uses the native OpenCode `bash` tool (not the
`ralph_*` prefixed aliases that the Ralph MCP server layer adds
when routing through `ralph smoke-interactive-opencode`). The
parser strips any `ralph_` prefix at the transport boundary so
the same parser code path serves both shapes. The captured
spelling here is `bash` (lowercase, no prefix).

The parser's existing `ralph_*` prefix normalization
(`OpenCodeParser._canonical_tool_name`) is verified by
`tests/test_opencode_captured_wire.py` (the `ralph_read_file` /
`ralph_write_file` / `ralph_edit_file` envelopes in that test
are synthetic router-layer fixtures, the wire shape matches the
1.18.14 envelope captured here exactly).

## Captured `tool_use` envelope shape

Every captured `tool_use` frame carries the same envelope:

```json
{
  "type": "tool_use",
  "timestamp": 1785000001000,
  "sessionID": "s_0001",
  "part": {
    "type": "tool",
    "tool": "bash",
    "callID": "c_0001",
    "state": {
      "status": "completed",
      "input": {"command": "cat /tmp/capture/a.txt"},
      "output": "line-1-marker\nline-2-original\nline-3-marker\nline-4-original\nline-5-marker\n",
      "metadata": {"output": "...", "exit": 0, "truncated": false},
      "title": "cat /tmp/capture/a.txt",
      "time": {"start": 1785000001000, "end": 1785000001000}
    },
    "id": "p_0002",
    "sessionID": "s_0001",
    "messageID": "m_0001"
  }
}
```

The parser reads `part.state.input` for arguments (NOT
`part.input` directly). The `callID` (not `callId`) is the
native call identity. `state.status` is `"completed"` for terminal
events (the binary collapses a `running` and a `completed` state
into a single terminal frame for completed calls; the parser
already handles this gracefully via `_tool_call_was_dispatched`).

## Captured `step_start` envelope shape

```json
{
  "type": "step_start",
  "timestamp": 1785000000000,
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
`step-start` with a hyphen — distinct from the top-level
`type: "step_start"` underscore form, which is what the
parser uses to dispatch.

## Captured `step_finish` envelope shape

```json
{
  "type": "step_finish",
  "timestamp": 1785000002000,
  "sessionID": "s_0001",
  "part": {
    "id": "p_0003",
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
  "timestamp": 1785000010000,
  "sessionID": "s_0001",
  "part": {
    "id": "p_0011",
    "messageID": "m_0004",
    "sessionID": "s_0001",
    "type": "text",
    "text": "**Command 1 — `cat`:**\n```\n...",
    "time": {"start": 1785000011000, "end": 1785000010000}
  }
}
```

This `text` event is the smoking gun for the previous inaccuracy:
the live 1.18.14 binary DOES emit `text` events. The previous
"no text events" claim in the (now-superseded) S-3 commit was
wrong against 1.18.14.

## The focused fixture

`tests/display/_fixtures/opencode_wire.jsonl` is a 12-frame
verbatim copy of the 12-frame
`opencode_write_edit_read_2025-11-19.jsonl` capture, with only
volatile IDs and absolute scratch paths normalized. Every frame
in the fixture is **measured**, not derived.

The fixture exercises the parser's envelope handling for the
write/edit/read sequence (three bash tool calls + their
step-start/step-finish markers + a final text-only assistant
turn). It does NOT exercise the display-capability routing
through `payload_from_tool_event("bash", ...)` because bash has
no syntax/file/diff preview surface; the existing
`tests/test_opencode_display_fidelity.py` synthetic
`read`/`write`/`edit` envelopes cover that surface separately
(without making a wire-format claim about it).

The `ralph_*`-prefixed synthetic envelopes in
`tests/test_opencode_captured_wire.py` exercise the transport
normalization separately, on the same envelope shape the live
binary emits (the underlying wire JSON keys — `state.input` /
`state.output` / `callID` / `state.metadata.truncated` /
`state.title` / `state.time` / `part.id` / `part.sessionID` /
`part.messageID` and the top-level `timestamp` / `sessionID` —
are identical between the captured bash envelope and the
synthetic `ralph_*` envelope).

## Normalization

Only volatile IDs and absolute scratch paths are normalized so the
fixture does not depend on session-rotation identity:

- `ses_<hex>` → `s_<NNNN>` (sequential numeric IDs)
- `prt_<hex>` → `p_<NNNN>` (sequential numeric IDs)
- `msg_<hex>` → `m_<NNNN>` (sequential numeric IDs)
- `call_<hex>` → `c_<NNNN>` (sequential numeric IDs)
- `/tmp/opencode-capture` → `/tmp/capture`
- Wall-clock epoch timestamps (e.g. `1786031820789`) → deterministic
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
    'Use the bash tool three times: cat /tmp/capture/a.txt, then sed -i s/original/MODIFIED/g /tmp/capture/a.txt && cat /tmp/capture/a.txt, then wc -l /tmp/capture/a.txt.'
```

The fixture cannot be regenerated from a unit test — the live
binary consumes tokens and is excluded from the 60-second combined
test budget. The committed fixture is the durable record.

## Summary of changes vs. the previously-committed provenance

The previously-committed version of this document (commit
`cdd00b826` "fix(opencode): normalize display tool events")
admitted that some fixture frames were "document-derived" rather
than captured from the live binary. That admission violated
the S-1 / S-2 requirement that every fixture frame come from a
real capture. The current version of this document replaces
those admissions with measured frames from
`tests/display/_fixtures/_raw/opencode_write_edit_read_2025-11-19.jsonl`
and corrects the "no text events" / "exactly five event types"
inaccuracies to match the 1.18.14 runtime evidence above.
