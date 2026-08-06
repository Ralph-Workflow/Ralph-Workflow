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
