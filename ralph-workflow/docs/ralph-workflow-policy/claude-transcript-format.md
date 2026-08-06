# Claude Code Transcript Format

The Claude Code interactive transport (`ralph/agents/parsers/claude_interactive.py`,
`ralph/agents/parsers/claude_interactive_transcript_parser.py`) and the
headless NDJSON transport (`ralph/agents/parsers/claude.py`) consume a
specific wire shape that has three pieces every future reviewer of the
idle-watchdog / subagent-visibility path must understand. Without these
three pieces, a reader hitting the same shape would re-discover the
same four root causes the wt-04-claude-parsing plan fixed.

## 1. The `subagents/` sibling directory

Claude Code 2.1.221+ writes subagent (sidechain) turns to a sibling
directory rather than inline in the parent transcript:

```
~/.claude/projects/<project-key>/<session-id>.jsonl              # parent
~/.claude/projects/<project-key>/<session-id>/subagents/
  agent-<agentId>.jsonl                                           # child transcript
  agent-<agentId>.meta.json                                       # {agentType, description, toolUseId, spawnDepth}
```

The parent's `tool_use` block carries the dispatch (the `id` field maps
to the child's `toolUseId` via `meta.json`). The child `.jsonl` carries
`isSidechain: true`, `agentId`, and `attributionAgent`. The parent
transcript only shows the dispatch / `tool_result` pair; the child's
`tool_use` / `text` / `thinking` events live in the sibling file. A
parser that only tails the parent transcript cannot see any of the
subagent's actual work — the parser sees the parent emit
`tool_use: Agent`, then sees nothing until the subagent returns, no
matter how much work the subagent did.

## 2. The synthetic-envelope classification rule

A `type: "assistant"` record whose `message.model == "<synthetic>"` (or
whose `message.usage.output_tokens == 0 AND message.stop_reason ==
"stop_sequence"`) is a bookkeeping envelope, not model output. Its
content text is often the literal `"No response requested."` and MUST
NOT be:

* emitted to the operator as agent text;
* included in any retry-context excerpt fed to the next prompt;
* counted as liveness evidence (the idle baseline MUST NOT reset).

Equally, a record whose `obj.isApiErrorMessage is True` (current
Claude Code 2.1.x placement) OR whose `message.isApiErrorMessage is True`
(legacy wire) is an API-error envelope. Same rule: route to the error
path, never to text / output / lifecycle / liveness.

The single source of truth is
`ralph/agents/parsers/_assistant_envelope.py: classify_assistant_record`,
which both transports call. **Key off the envelope, never off the
literal `"No response requested."` text** — a future Claude Code build
may rename the synthetic envelope text and the rule must still
classify it correctly. The four `test_claude_interactive_transcript_parser.py`
acceptance tests pin the top-level / nested / synthetic placements
across both transports.

## 3. The in-process subagent liveness evidence channel

Claude Code subagents are in-process: no PID is ever registered, so
`ProcessMonitor.live_subagent_count()` is permanently 0 for the
interactive transport. The watchdog's `record_subagent_work` channel
in `ralph/agents/idle_watchdog/_activity_methods.py` is the
in-process-only liveness feed. The subagent transcript tailer
(`ralph/agents/invoke/_subagent_transcript.py`) tails every
`agent-*.jsonl` discovered under the parent's `subagents/` directory
and forwards each parsed event through the same channel the parent's
events use. Without that feed, a fully-productive subagent is
indistinguishable from a hung process from the watchdog's point of
view, and the classifier returns `SILENT_SUBAGENT` (the gate fires).

The tailer is constructed lazily inside
`ralph/agents/invoke/_pty_line_reader.py:_transcript_thread` the
first time a parent `session` event is observed (NOT at reader-thread
start, which is before the visible-TUI extractor has populated
`_captured_session_id`). The parent thread wires three calls per
parsed event so the tailer's bookkeeping stays in lock-step with
the parent-side event stream:

* `note_parent_record(obj)` captures the Claude Code `version`
  from the first user / assistant record (used by the R7 diagnostic
  to name the build that emitted the layout).
* `note_dispatch(tool_use_id, tool_name)` probes the `subagents/`
  layout the moment a `tool_use:Agent` / `tool_use:Task` block lands
  (R7 dispatch-driven probe) and starts the tailer the first time
  a dispatch is observed.
* `note_completion(tool_use_id)` drops the matching child file the
  moment the parent's `tool_result` block lands (R1 lifecycle
  ownership). The child `toolUseId` is correlated via
  `agent-*.meta.json`; a child that appears AFTER the parent's
  `tool_result` (the fast-returning-child case) is dropped on its
  first discovery tick via the tailer's
  `_completed_dispatch_ids` registry.

The PID-based channel (`make_claude_interactive_subagent_pid_source`,
`ralph/process/monitor/_subagent_pid_source_providers.py:114`) is
correct for transports whose subagents really are separate processes
(OpenCode, Cursor) but is dead weight for the interactive Claude Code
transport. Do not delete the PID channel — other transports depend on
it — but do not rely on it to grade the interactive Claude Code
transport's liveness.

## Cross-references

* `tests/agents/invoke/test_claude_interactive_real_capture_replay.py`
  — replays the real captured session fixture; watchdog does NOT fire
  during the 12:52:45 → 12:58:04 subagent work window.
* `tests/agents/parsers/test_claude_interactive_transcript_parser.py`
  — pins the synthetic / error envelope classification on both
  placements (top-level and nested `isApiErrorMessage`).
* `docs/agents/watchdog-spec.md` — the R1–R8 acceptance criteria this
  transport feeds.
* `docs/ralph-workflow-policy/verification-policy.md` — the verify
  budget every wire-format change must respect.
