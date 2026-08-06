# AGY v1.1.10 stream-json wire-format provenance

This file is the git-tracked record of the measured AGY v1.1.10
`--print --output-format stream-json` wire format. It is the citation target
for `ralph/agents/parsers/agy.py`'s module docstring and for
`tests/test_agy_parser.py`'s module docstring — both previously pointed at
`ralph-workflow/tmp/agy-source-of-truth.txt`, which is git-ignored
(`ralph-workflow/.gitignore:43` `tmp/`; root `.gitignore` `tmp/`, `**/tmp/`)
and therefore never reaches a commit.

**Provenance note:** the observations below were captured against the live
`agy` binary (v1.1.10, authenticated account) during the planning pass that
produced the parsing-fidelity plan this fixture set implements. They are
reproduced here verbatim from that plan's `Characterization` section as the
durable, tracked record; this file does not represent a new capture taken by
the agent that authored the fixtures in this directory.

## Probed commands

- `agy --version` → `1.1.10`
- Binary present at `~/.local/bin/agy`; the account is authenticated
  (`~/.gemini/antigravity-cli/cli.log` shows successful `streamGenerateContent`
  calls).

## PTY requirement

AGY's `--print --output-format stream-json` mode writes stream-json frames to
stdout **only when stdout is a controlling terminal (PTY)**. Running the same
invocation with stdout redirected to a plain pipe (no PTY) produces **empty
stdout** — this is a real, measured constraint, not a bug in the capture
method. All three captures below were taken with a PTY.

## Capture harness

Each capture was taken with this PTY driver, run from a scratch directory:

```python
python3 - <<'EOF'
import os, pty, time, select
argv = ["agy","--output-format","stream-json","--dangerously-skip-permissions",
        "--add-dir",os.getcwd(),"--model","gemini-3.6-flash-low","--print","<prompt>"]
pid, fd = pty.fork()
if pid == 0: os.execvp(argv[0], argv)
buf=b""; end=time.time()+300
while time.time()<end:
    r,_,_ = select.select([fd],[],[],1.0)
    if r:
        d = os.read(fd, 65536)
        if not d: break
        buf += d
    elif os.waitpid(pid, os.WNOHANG)[0] == pid: break
open("pty_stdout.raw","wb").write(buf)
EOF
```

## The three probe prompts

1. `Reply with exactly the word OK` (text-only capture)
2. `Create a file named hello.txt in the current directory containing exactly
   hi, then read it back and stop.` (tool-using capture)
3. `Use your define_subagent and invoke_subagent tools to create and run two
   short-lived subagents in parallel: one that writes a.txt containing 'a',
   one that writes b.txt containing 'b'. Then stop.` (multi-subagent capture)

## Observed frame vocabulary

- `init` — carries a top-level `conversation_id` and an `init` object with
  `model`, `cwd`, `tools` (includes `ask_permission`, `define_subagent`,
  `invoke_subagent`, `manage_subagents`, and others), and `permission_mode`
  (e.g. `"always-proceed"`).
- `step_update` — carries `conversation_id`, `step_index`, `state`
  (`ACTIVE`/`DONE`), and `step_type`. Observed `step_type` values:
  `user_input`, `unknown`, `agent_response` (incremental `text_delta`),
  `tool` (`tool_name` / `tool_info.name`, `tool_info.parameters`, and on DONE
  `tool_info.output` when the tool produces one, plus `duration_seconds`),
  `checkpoint`, and `subagent` (`subagent_info.subagents`, a list of entries;
  each entry carries `type_name`, `role`, `initial_prompt` on ACTIVE, and
  additionally `conversation_id` + `log_uri` on DONE — the identity field is
  added only at completion, never at dispatch).
- `result` — carries `conversation_id`, `status`, `response`,
  `duration_seconds`, `num_turns`, and `usage` (token counts).
- `error` — the emitter exists in the binary
  (`streamJSONEmitter.EmitError`, alongside `EmitInit` / `EmitStepUpdate` /
  `EmitFinal`), but no live `error` frame was captured during the three
  probes above, so its exact payload shape is unmeasured. Fixtures and tests
  that exercise the `error` event use a synthetic frame and are labeled as
  such — they are not claimed as captured live.

Additional measured facts:

- PTY line endings are `\r\n`; no ANSI escapes appear in the stream-json
  stream.
- `text_delta` is genuinely incremental (e.g. `"I have creat"` followed by
  `"ed the file ...\n"` on the next update for the same step) — the DONE
  frame does not repeat the whole text, so accumulation across ACTIVE/DONE
  updates is required, not optional.
- Many tools produce **no** `tool_info.output` on their DONE update
  (`write_to_file` had none in the capture; `view_file` had
  `"2 lines, 3 bytes"`).
- Two subagents were observed sharing one `step_index` in both their ACTIVE
  and DONE frames.

## Real empty-output failure mode (v1.1.10)

The clean-exit-with-no-output failure mode prints on **stderr**:

```
jetski: no output produced — a tool required the "command" permission that
headless mode cannot prompt for, so it was auto-denied. ...
Alternatively, re-run with --dangerously-skip-permissions ...
```

and the CLI log (`~/.gemini/antigravity-cli/cli.log`) records a matching
symptom:

```
Print mode: timed out after 7 polls (printed=3)
```

`ralph/agents/_agy_upstream_diagnostic.py` recognizes both patterns (see
`_PERMISSION_AUTO_DENY_PATTERN` and `_PRINT_MODE_TIMEOUT_PATTERN`) and returns
an actionable message naming `--dangerously-skip-permissions` / the
`permissions.allow` setting as remediation.

## Fixtures in this directory built from these measurements

- `agy_wire_text.jsonl` — the text-only capture (prompt 1 above).
- `agy_wire_tool.jsonl` — the tool-using capture (prompt 2 above).
- `agy_wire_subagent.jsonl` — the multi-subagent capture (prompt 3 above).
- `agy_wire.jsonl` — kept for the pre-existing full-transcript replay test;
  content mirrors `agy_wire_tool.jsonl`.

Volatile values (UUIDs, absolute paths, `duration_seconds`) are normalized to
stable placeholders (`00000000-0000-0000-0000-00000000000N`, `/workspace/...`,
fixed durations); token/usage counts are kept as measured.

## B1/B2/B3/B4/B5 regression fixtures (parsing-fidelity plan, S-8/S-9)

The inline JSON fixtures added to `tests/test_agy_parser.py` for the B1
(duplicated tool name), B2 (raw-float duration noise), B3/B4 (dropped
bodiless frames / discarded per-step usage), and B5 (fake `call_id`) defect
locks are **synthetic, derived from the documented measured frame
vocabulary in this file** — they are not a new live capture taken by the
agent that authored them. Each is a small, hand-built variation clearly
traceable to the `step_update` shapes already recorded above (`tool`,
`agent_response`, `user_input`, `unknown`, `checkpoint`) and to the
already-committed `agy_wire.jsonl` / `agy_wire_tool.jsonl` /
`agy_wire_text.jsonl` fixtures replayed by the existing D1-D9 tests:

- **B2** (`test_b2_completion_summary_duration_rounds_to_two_decimals`,
  `test_b2_result_summary_duration_rounds_to_two_decimals`): synthetic
  `tool` and `result` frames carrying a 9-decimal `duration_seconds`
  (`0.076075017`, `3.581234567`) in the same shape as the measured
  `agy_wire_tool.jsonl` `duration_seconds` fields, to pin the 2-decimal
  formatting.
- **B5** (`test_b5_step_index_fallback_id_is_not_labeled_tool_use_id`,
  `test_b5_genuine_call_id_still_uses_tool_use_id_key`): synthetic `tool`
  ACTIVE/DONE pairs, one omitting `tool_info.call_id`/`.id` (the measured
  common case — AGY tools rarely carry one) to pin the `step_ordinal`
  metadata key, and one supplying a synthetic `call_id` value to pin that a
  genuine upstream id is not renamed.
- **B3/B4** (`test_b3_user_input_unknown_checkpoint_steps_emit_lifecycle_events`,
  `test_b3_bodiless_agent_response_done_with_no_buffered_text_surfaces_usage`,
  `test_b4_bodiless_agent_response_done_usage_reaches_pending_flush`): the
  first replays the exact `user_input` / `unknown` / `checkpoint` DONE-frame
  shapes already measured in `agy_wire_tool.jsonl` (step_index 0, 1, 4); the
  second is a synthetic bodiless `agent_response` DONE frame with no
  preceding ACTIVE delta; the third replays the already-committed
  `agy_wire_text.jsonl` one-shot `OK` capture, whose measured DONE frame
  carries an empty `text_delta` alongside `usage`.
- **B1** (`test_b1_orphan_tool_result_record_body_names_tool_without_duplicating_it`,
  `test_b1_correlated_tool_result_record_body_omits_tool_name_and_stays_nonempty`):
  synthetic `tool` ACTIVE/DONE pairs for `write_to_file` against a
  `TargetFile` parameter and a 9-decimal duration, in the orphan (no
  `call_id`) and correlated (synthetic `call_id`) shapes, run through the
  full `agy.py` -> `agent_event_renderer` -> `presented_entry.py` pipeline
  to pin the tool-name deduplication contract.

Also note (S-8 characterization): running this repository's test suite
against the current `presented_entry.py` showed its `_tool_result_record_body`
/ `_strip_leading_tokens` dedup logic already collapses a repeated leading
tool-name token to a single occurrence for both the orphan and correlated
branches (see `test_b1_*` above) — no code change was required in
`presented_entry.py` to close B1 on the *detailed-report* path; the
regression tests above pin that contract now that B5 routes ordinary AGY
tool results through the previously-rarely-exercised orphan branch (before
B5, the step-index fallback made `tool_call_id(metadata)` truthy for nearly
every AGY tool result, so the orphan branch was effectively dead code for
this parser).

## B6: bracketed model text eaten downstream (parsing-fidelity plan, S-8/S-10)

Root cause located, confirmed with a live `rich.text.Text.from_markup`
repro against the brief's exact measured example -- no new live capture
was needed since the parser's own output (a flushed `text` event) was
already known-correct per the brief; only the display-layer symptom was
unmeasured code, not an unmeasured wire shape. `[todo-list.js]` in
``"Full implementation at [todo-list.js](file:///workspace/todo-list.js)"``
has no matching `[/todo-list.js]` closing tag, so Rich's markup grammar
treats it as an *open* style tag that runs to end-of-string -- and
`Text.from_markup(text).plain` drops the tag's own bracketed content
entirely, not merely the bracket characters. This is
`ralph/display/line_sanitizer.py::strip_markup_safe`, the single choke
point both `_plain_constants._sanitize` and `parallel_display._strip_markup`
are required to delegate through (enforced by
`ralph/testing/audit_terminal_escape_containment.py`'s `FunctionBodyInvariant`
for `strip_markup_safe`), reached from the live-activity-line path via
`parallel_display.py`'s `_activity_lines` -> `_sanitize(snapshot.last_activity_line)`
at `parallel_display.py:2213`.

Fixed by gating the `Text.from_markup` parse on the presence of an actual
closing-tag marker (`"[/"`) in the input: genuine intentional Rich markup
is always written as a matched pair (`[bold]...[/bold]`) and is
unaffected; text with no `"[/"` anywhere (every measured AGY case --
markdown links, bracketed tool names, grep patterns) can contain no
matched pair and is returned literal without ever reaching the parser,
so an unclosed `[tag]`-shaped span can no longer be silently eaten. The
pre-existing adversarial case the choke point exists for (`[/pdf /text
/imageb]`, a lone unmatched *closing* tag with no preceding open) still
contains `"[/"`, so it still reaches `Text.from_markup`, still raises
`MarkupError`, and still falls back to the literal text unchanged --
that regression path is untouched by this fix. Regression coverage:
`tests/test_line_sanitizer.py` (`strip_markup_safe` unit tests covering
the unclosed-link case, the unmatched-closing-tag case, a genuinely
matched pair, and terminal-control stripping on the fast path) and
`tests/test_agy_parser.py::test_b6_bracketed_markdown_link_survives_to_the_rendered_activity_line`
(full `AgyParser` -> `normalize_event_from_agent_output_line` ->
`render_event_kind_text` pipeline, split across two `text_delta` chunks
exactly as measured).

**B1 follow-up: the live-activity-line duplication was real, and lived in
`ralph/display/agent_event_renderer.py`.** The brief's illustrative example
(`✓ PASS ↳ write_to_file write_to_file todo-list.js (0.076075017s)`) is the
*live* activity line, a separate rendering path from `presented_entry.py`'s
detailed report. `_render_tool_result_event` prepends `tool_ref` (the tool
name) as its own text segment, then appends the parser's result body
unmodified via `_append_tool_result_body` — which previously discarded the
`tool_name` argument entirely (`del tool_name`). Since `AgyParser`'s
`_completion_summary` (B2/B5's neighbor) synthesizes body content that
already starts with the tool's own label whenever a DONE frame carries no
`tool_info.output` (the common AGY case), the tool name doubled on every
such live line. Fixed by `_strip_duplicate_tool_prefix`, called from
`_append_tool_result_body`, which removes exactly one leading
case-insensitive duplicate of the tool name from the body before it is
appended — a body that does not start with the tool name (e.g. `view_file`,
which carries a real `tool_info.output`) is left unchanged. Regression
coverage: `tests/test_agent_event_renderer_tool_result_dedup.py`, driven
through the public `render_event_kind_text` entry point with content shaped
exactly like `AgyParser._completion_summary`'s synthesized output (measured
shape, not fabricated — see the B2 fixture note above for the same
duration-noise pattern).
