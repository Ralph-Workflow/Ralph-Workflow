# Real-capture fixtures for the Claude interactive transport

This directory holds real (redacted) Claude Code transcript fixtures
that the S-2 regression test (`test_claude_interactive_real_capture_replay.py`)
and the R5/R6/R7 acceptance tests replay against the production
parsers, watchdog classifier, and subagent tail.

## Provenance

Captured from session `a4731909-31bc-4ad5-bac9-cd59ee7e0615` on Claude
Code **2.1.223**, workspace
`/home/mistlight/Projects/Expeditions-Core/wt-02-contractor`, 2026-08-06
12:52-13:11 local. This is the actual session whose four-cycle
kill/resume burn motivated the rc1/rc2/rc3/rc4 root-cause fixes; the
fixtures are not hand-written. Per `AGENTS.md`, the canonical fixture
shape is **real captures, not synthetic frames**.

## Layout

```
-fixture-root/
  README.md                                              (this file)
  -home-mistlight-Projects-Expeditions-Core-wt-02-contractor/
    a4731909-31bc-4ad5-bac9-cd59ee7e0615.jsonl            (parent transcript, 63 lines)
    a4731909-31bc-4ad5-bac9-cd59ee7e0615/
      subagents/
        agent-a4ff0e1ce30e00726.{jsonl,meta.json}        (36 + meta)
        agent-a8b60e2c43648cb59.{jsonl,meta.json}        (133 + meta)
        agent-aa4510ad576b74f67.{jsonl,meta.json}        (107 + meta)
        agent-ae8172f08ddb4f463.{jsonl,meta.json}        (93 + meta)
        agent-aed7a541814eeea57.{jsonl,meta.json}        (130 + meta)
```

The first `-` directory component is the canonical Claude Code
project-key (`str(workspace).replace("/", "-")`), and the second is
the session id; the layout mirrors
`~/.claude/projects/<project-key>/<session-id>.jsonl` and its
sibling `subagents/` directory exactly so the existing
`find_claude_transcript_*` discovery helpers resolve the fixture
without any test-only branch.

## Redaction rules

Per-event fields preserved verbatim because they are the parser's
envelope contract:

- `isSidechain`, `agentId`, `attributionAgent`, `sessionId`, `version`
- `type`, `message.role`, `message.model`, `message.usage`,
  `message.content` shape, `message.id`, `message.stop_reason`,
  `message.stop_sequence`
- `tool_use.id`, `tool_use.name`, `tool_use.input` shape
- `tool_result.tool_use_id`, `tool_result.content` shape
- `agent-<id>.meta.json` content (carries `toolUseId`/`agentType`/
  `description`/`spawnDepth` -- the tailer must read these exactly)
- The `<synthetic>` model name on bookkeeping turns (verified
  verbatim against the captured envelope)

Fields removed because they are operator-bound or environment-bound:

- `cwd`, `promptId`, `requestId` on the top-level record
- Absolute path prefixes inside user-role `content[].text` blocks
  (`/home/mistlight/Projects/Expeditions-Core/...` is replaced with
  `/REDACTED_PARENT_DIR/...` and
  `/home/mistlight/Projects/Expeditions-Core/wt-02-contractor/...`
  with `/REDACTED_WORKSPACE/...`). The replacement is stable so a
  future operator can grep the fixture without leaking paths.

The redaction is implemented in `tmp/redact_fixture.py` at the repo
root. Re-running the script overwrites the fixture set with a fresh
copy; the script is intended for one-shot use at capture time, not
for re-running against different sessions. To capture a different
session, edit `SOURCE_ROOT` in the script and re-run.

## What the fixtures exercise

- Parent transcript has 5 `<synthetic>` bookkeeping turns (R4/R6
  acceptance: must NOT appear as agent text, must NOT reset the idle
  baseline, must NOT enter the retry-context excerpt).
- Subagent entries carry `isSidechain: true`, `agentId`, and
  `attributionAgent` (R3: attributed, not flattened).
- Subagent `.meta.json` files carry `agentType`, `description`,
  `toolUseId`, `spawnDepth` -- the R1 surface for correlating a
  child transcript back to the parent's `tool_use` block.
- The first pause-and-kill cycle (12:52:45 -> 12:58:04) is left
  uncut in `agent-ae8172f08ddb4f463.jsonl` so the S-2 regression
  test replays the actual timing without truncation.

## Refresh procedure

When Claude Code's wire format changes in a way that breaks these
fixtures:

1. Capture a fresh session from the live CLI (with operator approval
   per the no-fabrication rule).
2. Re-run `tmp/redact_fixture.py` with the new `SOURCE_ROOT`.
3. Update the "Captured from" line above with the new Claude Code
   version and timestamp.
4. Re-run `make verify`. If the S-2 / R5/R6/R7 tests fail on the
   new fixture shape, fix the parser first (the fixture is the
   source of truth), then re-run.
