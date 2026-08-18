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


## 2026-08-06: Evidence Provenance plan, S-1/S-2 -- AGY's real MCP config path

Captured live against `agy` v1.1.10 (`gemini-3.6-flash-low`) in this
workspace, run to completion end to end via
`uv run python -m ralph smoke-interactive-agy --agent agy/gemini-3.6-flash-low`
(S-1). This entry records what those runs actually showed, not an
interpretation from the plan.

**S-1 baseline reproduction (before any code change).** Reproduced the
brief's 2026-08-05 finding fresh in this workspace: `Verdict: DEGRADED
(host-synthesized)`, `EXIT_CODE=0` (the exit code and Breaks cell derived
only from `result.errors`, ignoring the graded verdict -- see PA-001/S-6
below). `init.tools` already listed `call_mcp_tool` (config-discovery half
of A1 was already wired), but the live transcript showed zero `tools/call`
attempts: `.agent/tmp/mcp-server.log` recorded the server starting and
exiting with no request lines in between, and `.agent/state.db`'s
`receipts`/`completion_sentinels` rows for the run carried `hmac = NULL`
(confirms A5: `RALPH_BROKER_SECRET` was unset in this shell). The model's
own transcript text: *"Since `ralph_submit_md_artifact` is unavailable in
the current toolset, saved the smoke test result artifact to
`.agent/tmp/smoke_test_result.md` as instructed for fallback."*

**S-2, first attempt: prompt-only fix.** Added an AGY-specific instruction
to `_build_smoke_prompt` naming `call_mcp_tool` as the required first
attempt (no argument shape hand-typed -- the model already holds that
schema). Two live re-runs against the *unchanged* config-write path still
took the fallback: one repeated the same "unavailable" claim, the other
switched to a stronger, directly falsifiable claim -- *"Since direct MCP
invocation tools (`call_mcp_tool`) are not present in the current
toolset..."* -- while that exact run's own `init` frame demonstrably listed
`call_mcp_tool`, and `mcp-server.log` again showed zero connection attempts
for the whole run window. The prompt-only fix could not be trusted on its
own: the model was reporting a tool as absent that the transcript's own
metadata said was present.

**S-2, root cause: two different global config paths.** AGY's own bundled
skill doc (`~/.gemini/antigravity-cli/builtin/skills/agy-customizations/docs/mcp_servers.md`)
names `~/.gemini/config/mcp_config.json` as the "Global Configuration" path
-- not `~/.gemini/antigravity-cli/mcp_config.json`, the path
`ralph.mcp.transport.agy` had been writing (and the only path its
"Research-confirmed facts" docstring listed). Writing the identical merged
Ralph entry to *both* paths (`_agy_secondary_config_path`, kept alongside
the original as `_agy_global_config_path`) and re-running the identical
prompt produced a genuine `call_mcp_tool` invocation: `✓ PASS ↳
call_mcp_tool ralph` with a real JSON tool result
(`{"artifact_type": "smoke_test_result", "valid": true, ...
"persisted_document": {...}}`), reproduced on two separate live runs.
Verdict moved from `DEGRADED (host-synthesized)` to `DEGRADED
(workspace-effect)` -- the artifact fact is now a real receipt matched to a
real tool call, not a promoted fallback; it is not `WIRE` only because
`RALPH_BROKER_SECRET` is unset in this shell (A5, by design -- an unsigned
server cannot produce a `WIRE` witness).

**S-2, completion step: measured, not assumed, to be unsafe to force.** An
initial version of this fix additionally instructed the model to route
`declare_complete` through a *second* `call_mcp_tool` call. A live replay
of that version did not return: the `agy` process had to be killed after an
inactivity timeout, and the run's own artifacts (`.agent/artifacts/
smoke_conformance_matrix.md`) show `artifact_submitted: ABSENT`,
`explicit_completion_seen: ABSENT` for that attempt -- worse than the
original defect, because it lost even the successful submission mid-run.
The completion instruction was reverted to the plain `declare_complete`
phrasing used by every other transport; the pre-existing AGY-only
`host_synthesized_sentinel` branch in `_run_smoke_agent` still covers a
model that does not complete on its own, and grades that outcome
`HOST_SYNTHESIZED` (honest, not a hang). A subsequent live run with the
reverted prompt had the model call `call_mcp_tool` a *second* time on its
own initiative anyway (having already learned the dispatcher works from the
first call) and this time it returned cleanly (`Task declared complete:
session_id=smoke-...`) -- moving `explicit_completion_seen` from
`HOST_SYNTHESIZED` to `TRANSCRIPT`. So the model can complete through the
dispatcher unprompted once it has evidence the route works; the fix does
not force the second call and therefore does not force the hang either.

**S-6 confirmed live.** That same run's harness output: `Verdict: DEGRADED
(workspace-effect)`, `EXIT_CODE=1`, and the Breaks cell rendered `degraded
verdict: DEGRADED (workspace-effect)` -- not the bare string `none` PA-001
named as the defect, and the exit code now agrees with the graded verdict
rather than with `result.errors` alone.

**Unexplained, out of scope.** Several of these live runs' transcripts
contained trailing text reading `[claude turn boundary]` followed by
`/exit`, immediately before AGY reported `FAIL timeout waiting for
response` / `agy result ERROR`. That text does not match any known AGY
`gemini-3.6-flash-low` output shape and does not correlate with anything in
this repository's own prompt construction; it appears to be a sandbox-level
artifact of this specific execution environment (unrelated processes
sharing the host), not a defect in `ralph`'s AGY transport or parser. Left
unexplained rather than guessed at; it did not prevent any of the runs
above from reaching and recording their real result.

**Still open / not attempted this pass.** The pre-existing stray `"ralph"`
entry in `~/.gemini/antigravity-cli/mcp_config.json` (noted as a risk in
the plan before this pass started) was still present after every run in
this pass, restored to the same dirty pre-run bytes each time (confirmed:
`agy_workspace_mcp_endpoint`'s restore-on-exit ran correctly and restored
exactly what it read at start; the underlying staleness predates this
session and was not chased further here, since the write behaviour it
restores is provably correct in isolation -- see
`tests/test_agy_workspace_mcp.py`). A genuinely unattended host-machine
cleanup of that pre-existing stray entry is out of scope for this plan.

## 2026-08-06: Evidence Provenance closeout plan, S-1/S-2 -- live confirmation (S-4)

Captured live against `agy` v1.1.10 (`gemini-3.6-flash-low`) in an isolated
`/tmp/agy-ws` workspace, two consecutive full runs of
`uv run python -m ralph smoke-interactive-agy --agent agy/gemini-3.6-flash-low`,
run with `RALPH_BROKER_SECRET` explicitly set this time (every prior entry
above ran with it unset). This is the first live capture in this file taken
with the secret present, and the first to close the loop all the way to a
genuine `WIRE`-graded `PASS`.

**S-1 confirmed live: the ledger now carries rows for handler-dict methods,
not only `tools/call`.** `.agent/tmp/mcp-wire-ledger.jsonl` in the scratch
workspace contained `initialize` and `tools/list` rows in addition to
`tools/call` rows in both runs (run 2: `{'initialize': 14, 'tools/list': 14,
'tools/call': 4}`). AGY's live transport does not exercise
`prompts/list` / `resources/list` / `resources/templates/list` /
`resources/read` in this prompt shape, so only two of the six handler-dict
methods are populated by a real run -- consistent with the plan's own
Expect clause ("the live agy transport may not exercise every one of the
six handler-dict methods in a single run, but any it does exercise must
appear chained").

**S-2 confirmed live: the ceiling line reaches the operator before the
final report table.** Both runs logged
`smoke: transport evidence ceiling is WIRE` (loguru INFO, emitted from
`_report_evidence_ceiling_once`) several lines before the Rich "Detailed
report" panel that carries the same fact as "Ralph tools advertised: WIRE";
in the captured run-2 log the ceiling line is at output line 29, the report
panel's matching line at 56.

**With the secret present, every required fact reached `WIRE` and the run
graded a genuine `PASS`.** Both runs: `Verdict: PASS`,
`Ralph tools advertised: WIRE`, `completion sentinel observed [WIRE]`
(`declare_complete` matched a `tools/call` ledger record), `tool activity
observed [WIRE]`, `smoke_test_result artifact submitted observed [WIRE]`
(receipt matched a `tools/call` ledger record) -- the full chain from A1
(dispatcher reachable) through A5 (HMAC-bound receipt) resolved to the top
rung in one run, something no entry recorded in this file before today had
measured (every prior `RALPH_BROKER_SECRET`-unset run topped out at
`DEGRADED (workspace-effect)` by construction, per F2/A5).

**The previously-documented sandbox artifact reappeared, unchanged.** Both
runs' transcripts again showed the `[claude turn boundary]` / `/exit` /
`FAIL timeout waiting for response` sequence described above, and the
process again required a watchdog kill. Consistent with the existing note:
it is a sandbox-level artifact of this execution environment, not a defect
in the transport or parser, and it did not prevent either run from reaching
and recording a real, complete `PASS` result (exit code 1 both times, from
the pre-existing "expected todo-list.js was not created" break this
artifact triggers by killing the process after the tool calls already
succeeded -- unrelated to S-1/S-2's ledger or ceiling-logging change).

## 2026-08-06: Evidence Provenance closeout plan, S-1 -- reconstructed 2026-08-05 transcript replay

`tests/_support/mock_agy.py::degraded_baseline_stream_json_lines` (and its
companion `degraded_baseline_artifact_markdown`) is a **reconstruction**
built from the product brief's own documented measurements of the
2026-08-05 baseline run (`.agent/PRODUCT_CRITERIA.md`'s "Measured baseline"
section and Workstream A) -- it is explicitly **not** a new live capture,
and **not** a byte-for-byte replay of the original
`.agent/raw/agy_gemini-3.6-flash-low.log` (16 JSON frames: 1 `init`, 14
`step_update`, 1 `result`), which was never committed to this repository.

The reconstruction reproduces the measured run's *shape* -- an `init` frame
advertising tools with no `ralph_*` / `call_mcp_tool` route, exactly 14
`step_update` frames (matching the brief's own cited frame count), a
`write_to_file` tool call as the transcript's only authoritative
tool-activity signal, and a closing `result` frame -- rather than the exact
byte content of the original capture. `tests/test_evidence_provenance_lattice.py::test_2026_08_05_transcript_replay_grades_degraded_host_synthesized`
feeds this reconstruction through the real `_run_smoke_agent` harness path
(not hand-built `Evidence` objects) and asserts the resulting
`SmokeRunResult` grades `DEGRADED (host-synthesized)` via
`grade_verdict(_required_evidence(result))` -- proving the grading
functions themselves (`_artifact_submission_evidence`, `_completion_evidence`,
`_tool_activity_evidence`, `transport_evidence_ceiling`), not just the
lattice arithmetic, derive that verdict from a transcript shaped like the
real run.

## 2026-08-06: Evidence Provenance closeout plan, S-3 -- fresh B-series live capture

`agy_wire_b_series.jsonl` is a **new, dedicated live capture** taken
specifically to close DoD items 8/9 for the B1/B2/B3/B5 parser-fidelity
regression tests, which previously constructed their frames as hand-written
JSON literals rather than replaying a captured fixture (flagged by DA-001
against the prior pass of this plan). It is a distinct capture from every
fixture in the "B1/B2/B3/B4/B5 regression fixtures" section above, which
remained synthetic derivations from the *documented* frame vocabulary; this
file's 12 frames are the direct, unedited output of one live run (only the
`conversation_id` and the scratch-workspace absolute path were normalized
to stable placeholders -- durations and usage are the exact measured
floats, kept deliberately un-rounded so the B2 rounding assertions exercise
genuine floating-point noise rather than a hand-picked example).

**Capture command** (the same PTY harness documented above, prompt 2 --
"the tool-using capture"):

```
agy --output-format stream-json --dangerously-skip-permissions \
    --add-dir /tmp/agy-capture-b --model gemini-3.6-flash-low --print \
    "Create a file named hello.txt in the current directory containing \
     exactly hi, then read it back and stop."
```

**Capture date:** 2026-08-06. **Binary version:** `agy` v1.1.10 (`agy
--version` printed `1.1.10` immediately before the capture, same shell).
Run to completion in a scratch directory (`/tmp/agy-capture-b`) with real
network access -- the process wrote `hello.txt` and exited on its own with
no watchdog kill required, unlike the sandbox-artifact runs recorded
elsewhere in this file.

The 12 captured frames, in order: `init` (58 tools, including
`call_mcp_tool`, zero `ralph_*` entries -- reconfirms A1), `user_input`
DONE (step 0), `unknown` DONE (step 1, carries `duration_seconds`),
a **bodiless `agent_response` DONE** with no `text_delta` but real `usage`
(step 2 -- B3/B4 shape), `write_to_file` tool ACTIVE/DONE (step 3, DONE
carries no `tool_info.output` and a 9-decimal `duration_seconds` of
`0.075956764` -- the B1/B2/B5 shape, and genuinely has no `call_id`/`.id`,
so it exercises the real step_index-fallback path), `checkpoint` DONE
(step 4, carries `usage`), a second bodiless `agent_response` DONE (step 5),
`view_file` tool ACTIVE/DONE (step 6, DONE carries a real `tool_info.output`
for contrast), a final `agent_response` DONE with `text_delta` (step 7),
and the closing `result` frame (`status: SUCCESS`, `duration_seconds:
2.586211531`, `num_turns: 1`).

`tests/test_agy_parser.py`'s `_fixture_lines` helper slices specific frame
indices out of this file so each B-series test replays exactly the real
frames its defect lock needs, in place of the previous hand-typed literals:

- **B3** (`test_b3_user_input_unknown_checkpoint_steps_emit_lifecycle_events`):
  frames 1 (`user_input`), 2 (`unknown`), 6 (`checkpoint`).
- **B2** (`test_b2_completion_summary_duration_rounds_to_two_decimals`,
  `test_b2_result_summary_duration_rounds_to_two_decimals`): frames 4-5
  (the `write_to_file` ACTIVE/DONE pair) and frame 11 (the `result` frame)
  respectively -- both now pin rounding against genuinely measured
  floating-point noise (`0.075956764` -> `0.08s`; `2.586211531` -> `2.59s`).
- **B5** (`test_b5_step_index_fallback_id_is_not_labeled_tool_use_id`):
  frames 4-5, asserting the real measured absence of `call_id`/`.id` routes
  through the `step_ordinal` fallback (`step_index` 3), not `tool_use_id`.
- **B1** (`test_b1_orphan_tool_result_record_body_names_tool_without_duplicating_it`):
  frames 4-5, run through the full `agy.py` -> `agent_event_renderer` ->
  `presented_entry.py` pipeline to pin the tool-name deduplication contract
  against a genuinely measured orphan (no-`call_id`) tool result.

The genuine-`call_id` companion tests (`test_b5_genuine_call_id_still_uses_tool_use_id_key`,
`test_b1_correlated_tool_result_record_body_omits_tool_name_and_stays_nonempty`)
remain synthetic and are deliberately left that way: across every live
capture recorded in this file (three prior probes plus this one), AGY's
`tool_info` has never once carried a genuine `call_id`/`.id` for an
ordinary tool, so there is no measured frame shape to replay for that
branch -- the companion tests exist to pin the code path that would handle
one if AGY ever starts emitting it.

**DoD items 8/9 status:** closed for B1, B2, B3, and B5 by this capture.
B4's dedicated tests (`test_b3_bodiless_agent_response_done_with_no_buffered_text_surfaces_usage`,
`test_b4_bodiless_agent_response_done_usage_reaches_pending_flush`) already
replayed a captured fixture (`agy_wire_text.jsonl`) before this pass and
were not touched. `test_s2_back_to_back_bodiless_usage_frames_merge_instead_of_overwriting`
remains synthetic by necessity: two bodiless `agent_response` DONE frames
landing back-to-back while text is still buffered was not observed in any
live capture (every measured run's bodiless DONE frames were separated by
an intervening tool step, as frames 2 and 5 of this very capture show), so
there is no live frame sequence to replay for that specific collision --
only the merge logic it locks in is real (see the S-2 entry above). B6 and
B7 were already resolved/labeled in earlier entries in this file and are
unaffected by this capture.

## 2026-08-06: Evidence Provenance closeout plan, S-9 / F5 -- ledger-backed capture table

`ralph/mcp/server/_wire_ledger.py` now ships a `WireLedgerCapture` /
`collect_captures` / `render_capture_table_markdown` helper triplet
(`tests/test_wire_ledger_capture_exporter.py` covers it
deterministically). The export contract:

- `collect_captures(workspace_root, secret)` reads the HMAC-chained
  `.agent/tmp/mcp-wire-ledger.jsonl` and returns every row that
  survives `verify_chain`. An unverifiable ledger backs nothing and
  returns `[]` (the F2 invariant expressed at the exporter surface).
- `render_capture_table_markdown(captures, run_id=...)` emits a stable
  markdown table (header row + body rows in input order, ISO-8601 UTC
  timestamps to second precision). A `run_id` filter keeps multi-run
  ledgers from bleeding into each other's capture tables.
- The `error`-frame B7 entry stays labelled synthetic until a real
  `error` row appears in a verified ledger -- the exporter does not
  invent one. No new live `error` frame has been captured for AGY in
  this pass; the existing B7 entry is left untouched per the brief's
  explicit instruction not to fabricate a payload shape.

Future live AGY runs can regenerate the capture-method table for the
newly-observed frames via `collect_captures(...).render_capture_table_markdown(...)`
without hand-editing this file; the prose sections above (and their
synthetic labels) are kept as the historical record of what each live
run actually showed.

## 2026-08-06: Evidence Provenance closeout plan, S-4 -- live planning-phase confirmation (DoD 13) through the *normal* pipeline

The S-1/S-2 entry above ("live confirmation (S-4)") proved `WIRE`-graded
`PASS` only through the smoke gate's own code path
(`smoke-interactive-agy`). DoD 13 is a distinct claim about the
*pipeline's ordinary planning phase* -- the exact phase that produced the
false `agy result SUCCESS` in the brief's measured 2026-08-06 baseline.
This entry closes that gap with a first genuine live capture.

**Setup correction to the plan's own S-4 step.** The plan's S-4 setup
bound `[agent_chains.planning]` via a project-local
`.agent/agents.toml` (the structured policy format). That file alone is
**not sufficient** on a machine that already has a user-global
`~/.config/ralph-workflow.toml` defining `[agent_chains]` /
`[agent_drains]` (the flat legacy format) -- which is the normal
post-`ralph --init` state, not an edge case. `ralph/policy/loader.py`'s
`_load_agents_policy_from_path` checks `_config_defines_agent_policy(config)`
*before* ever reading the project-local `agents.toml` path: when the
`UnifiedConfig` passed in by `ralph/cli/commands/run.py:288`
(`load_policy_for_workspace_scope(workspace_scope, config=config)`)
already carries non-empty `agent_chains`/`agent_drains` -- which it does
whenever a global `ralph-workflow.toml` defines them, per
`ralph/config/loader.py`'s five-layer merge (embedded defaults -> global
agents.toml -> global ralph-workflow.toml -> **project-local
`.agent/ralph-workflow.toml`** -> CLI flags) -- `build_agents_policy_from_config(config)`
is used instead, and the project-local *structured* `.agent/agents.toml`
is never read at all. A direct reproduction this session (calling
`load_policy_for_workspace_scope(scope)` with `config=None`, matching the
plan's own preflight-assertion script) reported the desired override
correctly; the **real** invocation path (`config=config`, matching
`run.py:288` exactly) did not -- it invoked `claude/sonnet` for the
planning phase, the global config's own default, silently ignoring the
project-local `agents.toml`. This is why the plan's preflight-assertion
script (which calls the loader with `config=None`) is not sufficient
proof of what a real `ralph run` will do on a machine with a populated
global config: it exercises a different code branch.

**The correct override for this scenario is the *legacy flat* project-local
file, `.agent/ralph-workflow.toml`** (not `.agent/agents.toml`), because
its `[agent_chains]` / `[agent_drains]` tables get merged into the
`UnifiedConfig` itself (project-local outranks global at layer 4 of 5, per
`ralph/config/loader.py`'s own documented merge order) -- so
`config.agent_chains['planning']` already reflects the override by the
time `_config_defines_agent_policy` runs, and `build_agents_policy_from_config`
faithfully reproduces it:

```toml
# .agent/ralph-workflow.toml
[agent_chains]
planning = ["agy/gemini-3.6-flash-low"]

[agent_drains]
planning = "planning"
```

With this file in place (and the project-policy-readiness preflight opted
out via `<!-- ralph-workflow-policy: skip -->` in AGENTS.md, so the
scratch workspace's 141 starter findings don't consume tokens on an
unrelated remediation agent before planning even starts), a direct
reproduction of `run.py:288`'s exact call
(`load_config(workspace_scope=scope)` then
`load_policy_for_workspace_scope(scope, config=config)`) printed
`resolved planning-phase agents: ['agy/gemini-3.6-flash-low']`, and the
subsequent live `ralph --quick` run (`RALPH_BROKER_SECRET` set, isolated
`/tmp` workspace, no `--developer-agent` override) invoked
`agy --output-format stream-json --dangerously-skip-permissions ...
--model gemini-3.6-flash-low --print .agent/tmp/planning_prompt.md` for
the planning phase, confirmed from the run log's own
`[activity] Invoking agent: agy/gemini-3.6-flash-low` line immediately
after `[phase] ◆ planning`.

**Case (a) of DoD 13 confirmed: the plan was submitted through the real
dispatcher.** The run's wire ledger (`.agent/tmp/mcp-wire-ledger.jsonl`)
carries, for run_id `17190e6f-6e70-4158-865a-9b92c7a1fe22` (the AGY
planning session), a `tools/call` row with `tool_name:
"ralph_submit_md_artifact"` at `timestamp: 1786038487.145`, followed by a
`tools/call` row with `tool_name: "declare_complete"` at `timestamp:
1786038488.766` -- both genuine, HMAC-chained records, not host-authored.
`.agent/state.db` carries a matching `receipts` row
(`run_id='17190e6f-...', artifact_type='plan',
created_at=1786038487.206`) and a matching `completion_sentinels` row
(`run_id='17190e6f-...', created_at=1786038488.766`), both HMAC-bound
(non-null `hmac` column, since `RALPH_BROKER_SECRET` was set). `.agent/PLAN.md`
and `.agent/artifacts/plan.md` both exist on disk.

**The phase report graded on the receipt, not the transcript.** The
agent's own transcript for this run ended in the same sandbox-timeout
artifact recorded in the S-1/S-2 entry above (`agy result ERROR`, cut
short by the same environment-level watchdog kill) -- yet the
`[phase-close]` panel immediately following reported `✓ Planning ◎ Cycle
1/1 (outer) → produced`, because that verdict is derived from the real
plan receipt and ledger record, not from the agent's own (this time
error-ending) closing summary. This is a direct, live demonstration of
DoD 14 as well as DoD 13: a broken transcript did not stop the phase from
reporting the true, receipt-backed outcome.

No new fixture file was added for this entry (unlike the B-series
captures above); the evidence lives in the (untracked, `/tmp`-scoped)
scratch workspace's own `.agent/tmp/mcp-wire-ledger.jsonl` and
`.agent/state.db`, reproduced verbatim in this entry's prose per the same
convention the S-1/S-2 entry above uses for its ledger-shape findings.

## 2026-08-14: AGY Transport Completion plan, S-8 -- v1.1.13 vocabulary re-measurement

`agy --version` on this machine now prints **1.1.13** (up from the 1.1.10
this file's earlier entries measured). Before running the live smoke
profile, the three original probe prompts were re-run with the same PTY
harness documented at the top of this file (scratch directories under
`/tmp`, `--dangerously-skip-permissions --model gemini-3.6-flash-low
--print`), and the observed frame vocabulary was compared against the
vocabulary recorded above.

**No drift in top-level events.** All three probes (text-only, tool-using,
multi-subagent) emitted only `init`, `step_update`, and `result` — the
same three top-level events every prior capture recorded.

**New `step_type`: `system_message` (bodiless).** The multi-subagent probe
emitted two `step_update` frames with `"step_type": "system_message"`
(step_index 8 and 12, both `state: DONE`), each carrying only
`conversation_id` / `step_index` / `state` / `step_type` — no `text_delta`,
no `tool_info`, no `subagent_info`, no `usage`. The text-only and
tool-using probes did not emit it; it appeared only around the subagent
completion boundaries of the multi-subagent run.

**Subagent identity now arrives at dispatch, plus `workspace_uris`.** In
every v1.1.10 capture, a subagent entry's `conversation_id` and `log_uri`
were present only on its DONE update. In the v1.1.13 multi-subagent
capture both fields (plus a new `workspace_uris` list) are already present
on the ACTIVE dispatch frame. This is additive: the parser's composite
`step_index:position` correlation key pairs ACTIVE -> DONE unchanged, and
the new field is preserved inside the event's `tool_info` metadata.

**Other additive changes (no parser impact).** `usage` dicts now carry
`thinking_tokens` and `cache_read_tokens` alongside the token counts, and
bodiless `agent_response` / `checkpoint` DONE frames additionally carry
`duration_seconds`. `init` (`model`, `cwd`, `tools`, `permission_mode`) and
`result` (`status`, `response`, `duration_seconds`, `num_turns`, `usage`)
shapes are unchanged.

**Parser consequence (the S-2/S-3 feedback loop).** The pre-change parser
silently dropped the new `system_message` frames: `_dispatch_bodiless_step`
surfaced only an enumerated set of bodiless step_types
(`user_input` / `unknown` / `checkpoint`) and dropped every other bodiless
frame that carried no `usage`. Fixed by replacing the enumeration with the
generic rule that ANY bodiless `step_update` whose `step_type` is not
`agent_response` (the text step, governed by the B4 usage-carry contract)
yields a non-empty `lifecycle` event, so step vocabulary added by a future
AGY release degrades observably instead of disappearing. Regression
coverage: `tests/test_agy_parser.py::test_v1_1_13_system_message_step_surfaces_as_lifecycle_event`
(replays the captured fixture below),
`test_future_unknown_bodiless_step_type_surfaces_as_lifecycle_event`
(synthetic future step_type, labeled as such), and
`test_v1_1_13_subagent_active_entries_carry_identity_and_workspace_uris`
(pins the additive subagent-entry fields and the unchanged correlation).

**Fixture.** `agy_wire_v1_1_13.jsonl` in this directory is the normalized
multi-subagent v1.1.13 capture (18 frames, the full run including both
`system_message` frames, the `workspace_uris`-carrying subagent
ACTIVE/DONE pair, and the closing `result`). Volatile values are
normalized exactly as the earlier fixtures: real conversation ids and the
`~/.gemini/antigravity-cli/brain/...` log URIs to stable placeholders, the
scratch `/tmp` workspace path to `/workspace`; durations and usage counts
are kept exactly as measured.

## 2026-08-14 (later): live interactive smokes -- `error_message` step_type

While grading the live smoke profile (same plan, S-8), both
`ralph smoke-interactive-agy` runs (regular and `--subagents`, live
v1.1.13, `--model gemini-3.6-flash-low`) emitted a `step_update` frame
with `"step_type": "error_message"` that none of the earlier captures
recorded. Retained verbatim in the run's raw stream
(`.agent/raw/agy_gemini-3.6-flash-low.log`): one bodiless frame —
`conversation_id` / `step_index` (8) / `state: DONE` / `step_type` only,
no `text_delta`, no `tool_info`, no `subagent_info` — arriving
mid-conversation between two `agent_response` steps, NOT adjacent to the
run's closing frames. The run continued normally afterwards (further
`tool` and `agent_response` steps, then a well-formed `result`), so the
step does not mark a fatal turn: AGY emits it for a recoverable
mid-conversation error (in both runs the display layer paired it with
the parser's eventual `timeout waiting for response` classification
after the harness's `/exit`).

**No parser change required.** `_dispatch_bodiless_step`'s generic rule
(any bodiless `step_type` other than `agent_response` yields a
`lifecycle` event) already surfaces it — both live transcripts show
`step error_message` as a lifecycle event, which is exactly the
degrade-observably behavior the rule was written for. Recorded here so
the observed-vocabulary list above stays complete: the measured
`step_type` set for v1.1.13 live runs is `user_input`, `unknown`,
`agent_response`, `tool`, `checkpoint`, `subagent`, `system_message`,
and `error_message`.

## 2026-08-18: v1.1.14 live smoke re-characterization

`agy --version` printed **1.1.14** immediately before the command below:

```bash
uv run python -m ralph smoke-interactive-agy --agent agy/gemini-3.6-flash-low
```

Ralph invoked AGY as `agy --output-format stream-json
--dangerously-skip-permissions --add-dir <workspace> --model
gemini-3.6-flash-low --print <prompt>`. The live display showed `init`,
`user_input`, `checkpoint`, `agent_response`, and `tool` activity; it
reported **21 parser events**, a WIRE-grade artifact receipt, a WIRE-grade
completion sentinel, and WIRE-grade tool activity. The smoke report's
verdict was `PASS`, but the command exited `1` because the closing AGY
result frame reported `status=ERROR` after the successful receipt and
sentinel. This is retained as an upstream/environmental closing-frame
failure, not converted into a green command result.

The command output did not expose a new JSON payload shape beyond the
operator-visible vocabulary above, and no raw stream file survived in the
scratch smoke directory. The existing normalized v1.1.13 fixtures remain
the tracked wire capture; the parser's generic bodiless-step fallback
continues to surface additive `step_type` values visibly rather than
silently discarding them.
