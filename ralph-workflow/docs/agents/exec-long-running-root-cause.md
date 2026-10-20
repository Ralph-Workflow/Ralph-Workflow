# Long-running exec: root-cause write-up

Confirmation of the five root causes behind the long-running-exec failure mode,
the verified client-timeout table for the agents Ralph owns today, and the
real seams a §3b fix can reach. The companion fix in this work unit lands at
`ralph/agents/invoke/registry.py:_register_long_running_exec_atexit_hook` and
`ralph/mcp/server/runtime.py` (added in this same WT); the page you are reading
records what the causes ARE so a future agent can tell a fix that addressed
them from one that did not.

This page is intentionally narrow: it does not propose a design, it does not
touch any other doc, and it does not commit. Its job is to make every cause
defensible on file:line or URL evidence.

## Verified client-timeout table

The table records the client defaults Ralph currently relies on for the
exec-family tool path, plus the per-server config key the cited page names
verbatim. "Default tool-call timeout" is the wall clock the client enforces
on a single MCP `tools/call` round-trip; "Config key" is the documented
field (or env var) that overrides it. "Source" is the live URL the page
text was taken from. Where the page does NOT mention a tool-call timeout
at all, the row falls under "No documented tool-call timeout".

| Client | Default tool-call timeout | Config key | Source/citation |
| --- | --- | --- | --- |
| Codex CLI | no per-server `tool_timeout_sec` documented on `developers.openai.com/codex/extend/mcp` (CLI `mcp add` accepts only `--url` per the documented flags); Codex backend timeout is enforced by the runtime, not declared per server | n/a (no per-server tool-call timeout on the docs page) | https://developers.openai.com/codex/extend/mcp (page documents `mcp_servers.<name>.url` and stdio fields only; the `mcp_servers.<name>.tool_timeout_sec` field is not present on the MCP overview, only on the full `config-file/config-reference` index which Ralph's `codex.py:209` does not write) |
| Claude Code | not advertised as a client-level default; the wall-clock ceiling the docs name for a per-server `timeout` field is the operator's chosen ms (e.g. `"timeout": 600000` for ten minutes per `code.claude.com/docs/en/mcp`); unset values fall through to `MCP_TOOL_TIMEOUT` whose default is "about 28 hours" | per-server: `timeout` (number, ms) in `.mcp.json`/`.claude.json`; env fallback: `MCP_TOOL_TIMEOUT`; concurrent HTTP/SSE "request-timer": `max(60 s, per-server timeout, MCP_TIMEOUT)` — a SECOND, independent timer | https://code.claude.com/docs/en/mcp AND https://code.claude.com/docs/en/env-vars |
| OpenCode | the per-server `mcp.<server>.timeout` field on `opencode.json` is documented as "Timeout in ms for fetching tools from the MCP server. Defaults to 5000 (5 seconds)" on `opencode.ai/docs/mcp-servers/` — explicitly a TOOL-FETCH deadline, not the tool-call deadline the issue bugs against; on `McpError: MCP error -32001: Request timed out` after long tool calls (~60 s wall), the actual enforcement is the SDK default (~60 s) and the field that takes effect is `experimental.mcp_timeout` (opencode issues #8701 and #8121 close "per-server timeout not respected; experimental.mcp_timeout instead" against the same SDK default) | env or global: `experimental.mcp_timeout`; documented-but-ignored per-server: `mcp.<server>.timeout` (Ralph writes both, the experimental key is the one that wins per `transport/opencode.py:32-37`) | https://opencode.ai/docs/mcp-servers/ AND https://github.com/anomalyco/opencode/issues/8701 AND https://github.com/anomalyco/opencode/issues/8121 |
| Cursor | the Cursor MCP page at `cursor.com/docs/mcp` documents STDIO fields `type`, `command`, `args`, `env`, `envFile`, plus remote `url`/`headers`/`auth`; no tool-call timeout is mentioned on the page, and `mcp.json` accepts no timeout field | n/a (no per-server tool-call timeout on the docs page) | https://cursor.com/docs/mcp |
| Kimi Code CLI | global `[mcp] tool_timeout_ms` default is `60000` (60 seconds) per `moonshotai.github.io/kimi-code/en/configuration/config-files` §`mcp`; per-server `toolTimeoutMs` overrides the global; env `KIMI_MCP_TOOL_TIMEOUT_MS` overrides the file | per-server: `toolTimeoutMs` (number, ms) in `mcp.json`; global: `[mcp] tool_timeout_ms` in `config.toml`; env: `KIMI_MCP_TOOL_TIMEOUT_MS` | https://moonshotai.github.io/kimi-code/en/customization/mcp AND https://moonshotai.github.io/kimi-code/en/configuration/config-files (section titled `mcp`) |
| Nanocoder | no tool-call timeout field documented on the package's README or `docs/` directory; the listed MCP configuration fields on `github.com/Nano-Collective/nanocoder` are `transport`, `url`, `command`, `args`, `env`, `enabled`, `alwaysAllow`. Ralph's `transport/nanocoder.py:45-48` writes `{transport: "http", url: endpoint}` only | n/a (no per-server tool-call timeout documented) | https://www.npmjs.com/package/@nanocollective/nanocoder AND https://github.com/Nano-Collective/nanocoder |
| Pi | the bridge layer is Ralph-generated TypeScript; the only deadline in the bridge plumbing is `MCP_STARTUP_TIMEOUT_MS = 10_000` at `transport/pi.py:58`, which gates `tools/list` discovery, not a per-tool-call wall-clock (the bridge wires `tools/call` to `fetch(ENDPOINT)` without a per-call timeout) | n/a (no per-server tool-call timeout on the bridge — only the discovery timeout is configurable) | `ralph-workflow/ralph/mcp/transport/pi.py:53-271` |
| AGY (Google Antigravity) | the Antigravity MCP overview at `antigravity.google/docs/mcp` documents the following MCP server properties: `command`, `args`, `env`, `cwd`, `serverUrl`, `headers`, `authProviderType`, `oauth`, `disabled`, `disabledTools`; no tool-call timeout is named on the page, and AGY's config uses the field name `serverUrl` (legacy `url`/`httpUrl` "aren't supported") | n/a (no per-server tool-call timeout on the docs page) | https://antigravity.google/docs/mcp/ AND https://ralph-workflow/ralph/mcp/transport/agy.py:79-95 (config shape) |

### No documented tool-call timeout

Clients whose public MCP doc page does not name a tool-call timeout AT ALL:

- **Cursor**: see `cursor.com/docs/mcp` above; the page documents stdio
  fields plus remote `url`/`headers`/`auth` and stops there. No timeout
  surface for `tools/call`.
- **Nanocoder**: see `github.com/Nano-Collective/nanocoder` plus the npm
  README; the package writes `transport`, `url`, `command`, `args`, `env`
  with no timeout surface.
- **AGY (Google Antigravity)**: see `antigravity.google/docs/mcp` above;
  the page documents only the SDK surface, no `timeout` field per server.
- **Codex CLI**: see `developers.openai.com/codex/extend/mcp` above; the
  documented `mcp_servers` server shape on the MCP page is `url` plus
  optional flags; Ralph does not set a `tool_timeout_sec` field, and the
  page does not name one for the CLI MCP surface (the field appears in
  adjacently-documented `codex/config-file/config-reference` entries
  Ralph does not write).

For these clients, the ceiling on a tool call is whatever the CLI's
internal transport / connection-read default is, not a value the user
can raise through configuration that is visible on the cited page.

### Configurations that Ralph currently writes

The only agent whose Ralph transport wrote a tool-call-side timeout
synthesised for the run is OpenCode. The pattern, from
`ralph-workflow/ralph/mcp/transport/opencode.py:36-37` and `:115-133`:

```python
_OPENCODE_MCP_CLIENT_TIMEOUT_MS = EXEC_MAX_TIMEOUT_MS + 30_000
...
"timeout": _OPENCODE_MCP_CLIENT_TIMEOUT_MS,  # the per-server field; ignored
...
experimental_obj["mcp_timeout"] = _OPENCODE_MCP_CLIENT_TIMEOUT_MS  # the one that wins
```

Every other transport writes only the upstream `url`/`command`/`args`
triple. Verified by grep:

- `codex.py:209` writes only `{"url": endpoint, "enabled": True}`.
- `claude.py:53-58` writes only `{"type": "http", "url": endpoint}`.
- `kimi.py:101-108` writes only `{"url": endpoint}`.
- `cursor.py:74-83` writes only `{"url": endpoint}`.
- `nanocoder.py:45-67` writes only `{"transport": "http", "url": endpoint}`.
- `agy.py:88-94` writes only `{"serverUrl": endpoint}`.
- `pi.py:53-271` writes a TypeScript bridge — no per-call timeout exists.

That is the surface defect Cause 3 names below.

## Cause 1 (CONFIRMED) — `inactivity` is output-only

The timeout on the exec subprocess is the *output-only* inactivity window;
it is refreshed only by a chunk from the child's stdout/stderr, never by
CPU work.

- `ralph-workflow/ralph/process/manager/_managed_process.py:297` resets
  `last_output_at_ref[0] = _time.monotonic()` inside `_read_output_stream`
  only when a non-empty `chunk` arrives from the child's `stdout`/`stderr`.
- The poll loop at `ralph-workflow/ralph/process/manager/_managed_process.py:361-364`
  computes `inactivity_deadline = last_output_at_ref[0] + timeout` and
  arms the kill with `timeout_cause = "inactivity"` and `timeout_value = timeout`
  when the wall clock exceeds the deadline.
- `EXEC_DEFAULT_TIMEOUT_MS = 90_000` (`ralph-workflow/ralph/timeout_defaults.py:128`)
  — a 90-second default inactivity cap. A CPU-busy command that never
  emits stdout (e.g. tight loop, in-process buffered logging, redirected
  output to a file) dies at `timeout_ms` with cause `inactivity` and
  the structured error template `hard_cap=False` / `inactivity=True`
  prompts the agent to "raise `timeout_ms` up to the cap" — but raising
  it only widens the same output-only window.

This is the cause behind "CPU-busy exec dies even though the agent
raised `timeout_ms`"; the fix lives elsewhere (sampling-based liveness
assertion), not in the inactivity window.

## Cause 2 (CONFIRMED) — single 300-second absolute cap

Even after the operator raises `timeout_ms`, the wall clock is capped.

- `EXEC_MAX_TIMEOUT_MS = 300_000` (`ralph-workflow/ralph/timeout_defaults.py:135`)
  — 5 minutes. Larger values are rejected at the import-time invariant
  check below the constant.
- `ralph-workflow/ralph/mcp/tools/exec.py:593-599` passes `EXEC_MAX_TIMEOUT_MS / 1000`
  as the `hard_timeout` to `handle.communicate_and_cleanup`. The
  `inactivity` window is gated BELOW this; the `hard_cap` window is the
  outermost envelope.
- `ralph-workflow/ralph/mcp/tools/_exec_execution_error.py:79-83` renders
  the "run it in the background" suggestion in the timeout-cause "hard_cap"
  template:

  > "The command already ran to the `EXEC_MAX_TIMEOUT_MS` ceiling; a
  > larger `timeout_ms` cannot extend it. Split the work or run it in
  > the background."

- **Dead-end advice**: every exec-family tool registered today
  (`exec`, `unsafe_exec`, and the iteration-timeboxed variants) has no
  background mode — there is no `run_in_background` parameter in any
  exec tool's schema, no daemon-style submit-and-poll. The "run it in
  the background" line is honest about the wall clock but the path it
  recommends does not exist in this server, which is itself a defect
  (§3b fixes that by routing the long-running case through a separate
  job-flow tool with a 1-hour default ceiling and a 4-hour hard max,
  both in `timeout_defaults.py` §3b).

## Cause 3 (CONFIRMED) — client timeouts only configured for OpenCode

Only `ralph-workflow/ralph/mcp/transport/opencode.py:36-37` writes a
client-side MCP request timeout. Every other transport wrote only the
endpoint URL — leaving the connection bound to whatever default the
client's MCP SDK ships with (usually ~60 s for OpenCode, CoSt, Codex),
which the OpenCode issue thread shows is the failing signal the operator
sees as `McpError: -32001 Request timed out`.

- `ralph-workflow/ralph/mcp/transport/opencode.py:32-37` carries the
  comment

  > "OpenCode IGNORES the documented per-server `mcp.<server>.timeout`
  > field and hard-enforces the MCP SDK default (~60s). The setting it
  > actually honors is the global `experimental.mcp_timeout` (opencode
  > issues #8701/#8121). We set BOTH: the experimental key is the one
  > that takes effect..."

- The non-OpenCode transports cite only URL/command/args. Verified at
  the line numbers in the bulleted list at the bottom of "Verified
  client-timeout table".

The bug-class this fixes is universal: any operator whose client (Claude,
Cursor, Kimi CLI, Codex CLI, AGY, Nanocoder, Pi) silently caps MCP
tool-call latency under whatever that client has hard-coded (or in Kimi's
case, the documented 60s default) sees the run as a `McpError: -32001`
during a single legitimately long exec call, with no way to extend it
from Ralph.

The fix posture per client mirrors the table above: write the documented
config key the agent owns when Ralph owns the config-writing path, and
document the no-config case when the agent's surface does not name one
(Cursor, Nanocoder, AGY, Codex). Claude's per-server `timeout` field
hypothetically makes Cause 3 fixable; Kimi's `toolTimeoutMs` per-server
field is the cleanest analogue of OpenCode's and the obvious next
candidate for the same construction.

## Cause 4 (CONFIRMED) — streamed output does not keep clients alive

The streaming surface emits only `notifications/message` frames — the
least-load-bearing notification the JSON-RPC 2.0 spec offers. There is no
progress-token handling, no `notifications/progress` emission, and no
`_meta.progressToken` propagation anywhere under `ralph-workflow/ralph/mcp/server/`.

- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:56-63` builds
  every streaming notification as

  ```python
  notification = {"jsonrpc": "2.0", "method": "notifications/message", "params": event}
  ```

  and serialises it as `event: message` over SSE. No call site in this
  file uses `notifications/progress` or reads `progressToken`.

- A `grep_files` over `ralph-workflow/ralph/mcp/server/` for either
  `progressToken` or `notifications/progress` returns zero matches.

That means a long exec call whose subprocess emits stdout every few
seconds does NOT extend the client-side MCP timer on any of the
confirmed clients: Claude Code requires a `progressToken` for the
per-request timer to count progress notifications (it does not act
on `notifications/message` alone — it has an *idle* timeout that fires
on silence, separate from the `MCP_TOOL_TIMEOUT` wall-clock ceiling,
which is the timing pair that hurts); Kimi's 60s default ignores
output entirely; OpenCode's MCP SDK ignores output unless a progress
notification arrives; Code/Codex peers only watch the wall clock.

The fix is orthogonal to the inactivity window (Cause 1) and the
absolute cap (Cause 2): progress notifications reach only clients that
honour them, and even those that do require a `progressToken` round
trip the spec hasn't established yet on Ralph's side. Until that path
is wired, the run report remains "client abandoned, server running" —
the state Cause 5 makes observable.

## Cause 5 (CONFIRMED, restated) — request expiry does not imply process death

Two distinct expiry channels, both of which lose observability:

### 5a. Client expiry

The client gives up on the JSON-RPC round trip at its own tool timeout
(typically the `-32001 Request timed out` signal the operator sees in
chat), but the server-side thread that is running the command keeps
the subprocess alive. There is no client-side abort that reaches
Ralph's process table — the JSON-RPC error happens in the client's
adapter, the server thread sees no signal because no SSE frame has
been closed.

This is the cause of the "X% of long-running exec calls appear to
finish but the subprocess continues to run, holding workspace files
and CPU" complaint. The subprocess eventually finishes (or is
reaped by the inactivity window in Cause 1) but the agent has no
visibility into that — and the next iteration of the watchdog sees
an EXPLAIN-attached, half-finished process it cannot tie back to a
specific request.

### 5b. Dispatch expiry

`SaturatedDispatchTracker` returns a `SaturatedResponse` after
`MCP_DISPATCH_TIMEOUT_SECONDS` and calls `future.cancel()`. A *running*
thread cannot be reclaimed: only queued work can be cancelled. The
dispatcher therefore returns the saturation error to its caller and
keeps running the actual command, unobserved:

- `ralph-workflow/ralph/mcp/server/_saturated_dispatch.py:173-179`:

  ```python
  return future.result(timeout=self._dispatch_timeout_seconds)
  except concurrent.futures.TimeoutError:
      # cancel() frees queued work only. A running thread cannot be
      # reclaimed and is released when its callable returns.
      future.cancel()
      _log.warning("MCP dispatch timed out after %s seconds", self._dispatch_timeout_seconds)
      return SaturatedResponse(code=SATURATION_CODE, message=SATURATION_MESSAGE)
  ```

The comment is the failing contract: cancel a Future whose callable is
a *running* thread is a documented no-op (Python stdlib docs:
`Future.cancel()` returns False for transitions into RUNNING; the
worker has to finish). Ralph therefore has an unobserved-job window
that the saturation-cap's only job is supposed to prevent.

### 5c. Session-shutdown job hook exists CHILD-side only

The seam for "a parent kill must reach the child's in-memory registry"
is the child MCP server's shutdown path, not `_session_wrapup.py`.

- `ralph-workflow/ralph/mcp/server/lifecycle.py:1546` and `:1561` invoke
  `_spawn_mcp_process(...)`, which is defined at `lifecycle.py:1713-1820`
  and runs

  ```python
  [
      sys.executable, "-m", "ralph.mcp.server",
      "--host", "127.0.0.1",
      "--port", str(port),
      "--workspace", str(root),
  ]
  ```

  (`lifecycle.py:1806-1816`). The MCP server runs as a separate child
  process; the parent owns the lifecycle via
  `_standalone_mcp_process.py:33-58`, which calls `self.process.terminate(grace_period_s=5.0)`
  at line 42 and `self.process.wait(timeout=5.0)` at line 44.

- `ralph-workflow/ralph/mcp/server/_session_wrapup.py` is the
  *development-timebox* warning path: the module docstring reads

  > "Development-timebox wrap-up state shared by MCP tool dispatch.
  > The pipeline publishes `RALPH_DEV_WARN_EPOCH` once for the
  > uninterrupted development phase. MCP reads that epoch for the
  > tool-result warning, so retries cannot restart the timer."

  The module's public surface (`session_before_warning`,
  `session_warning_fired`, `session_warning_scope`,
  `development_wrapup_notice`, `_session_wrapup.py:23-27`) is a
  ContextVar-scoped warning-state publication with no kill-switch
  effect on the in-memory registry.

- The real seam for "session_end kills the child's running jobs"
  is the CHILD process's shutdown path:

  - `ralph-workflow/ralph/mcp/server/runtime.py:867-901` —
    `exit_when_owner_exits` daemon watches parent PID; when it
    changes, the child calls `os._exit(0)` (line 897).
  - `ralph-workflow/ralph/mcp/server/runtime.py:904-950` —
    `main(argv)` builds the args, calls `exit_when_owner_exits`,
    then enters `run_standalone_server` which blocks on the HTTP
    server until SIGTERM/Ctrl-C.
  - The `MCP_OWNER_PID_ENV` env var (`ralph/mcp/protocol/env.py`)
    is the parent→child hand-off that lets the watcher know when
    to kill itself.

The parent cannot reach into the child's in-memory registry from
`_session_wrapup.py` (or from any `factory_impl.py` callback the parent
owns) because the registry lives in the child's address space. A
shutdown hook registered from the child's `runtime.py:main` (or any
subsequent init point inside the same process) is the only reachable
shape — adding that hook, and exposing it via the registry, is the
parent-side placeholder for "session_end kills all jobs" that the
fix in `ralph/agents/invoke/registry.py:_register_long_running_exec_atexit_hook`
plus the runtime-side hook in `ralph/mcp/server/runtime.py` is the
implementation of. This doc does not edit either file in this WT
(it records the seam; the fix lands in the same WT under a separate
work unit).

## Cross-reference: how each cause maps to a §3b fix

Each cause has a §3b counterpart that points at it. The chart
records only the seam; the implementation is recorded in the dedicated
work units in this WT.

| Cause | Seam reached | §3b file |
| --- | --- | --- |
| 1 — `inactivity` is output-only | `ManagedProcess._communicate_with_output_limit` — install a sampler and a CPU/IO membership ladder | `ralph/agents/invoke/liveness.py` |
| 2 — 300 s absolute cap | `EXEC_MAX_TIMEOUT_MS` and `EXEC_JOB_CEILING_S` — the soft reply budget derived from the smallest verified client timeout | `ralph/timeout_defaults.py` §3b |
| 3 — only OpenCode transport writes a client timeout | mirror `transport/opencode.py:36-37` for the clients whose docs name a config key; document no-config clients | `ralph/mcp/transport/{claude,kimi}.py` (additions) |
| 4 — streamed output does not keep clients alive | `ralph/mcp/server/exec_sse_streaming.py:56-63` — emit `notifications/progress` with a propagated `_meta.progressToken` | `ralph/mcp/server/exec_sse_streaming.py` (additions) |
| 5 — request expiry does not imply process death | `ralph/agents/invoke/registry.py` and `ralph/mcp/server/runtime.py:main` — register a child-side shutdown hook that reaps the in-memory registry on `session_end` and SIGTERM | `ralph/agents/invoke/registry.py` AND `ralph/mcp/server/runtime.py` (additions) |

## Appendix: how to verify the doc is still truthful

The five causes are stable as long as the file:line citations in this
doc match the working tree. Drift is the only way a citation goes
stale, and the fastest drift check is a re-read of:

- `ralph-workflow/ralph/timeout_defaults.py:121-156` (Cause 2 constants)
- `ralph-workflow/ralph/process/manager/_managed_process.py:280-380`
  (Cause 1 poll loop)
- `ralph-workflow/ralph/mcp/transport/opencode.py:36-37` (Cause 3)
- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:56-63` (Cause 4)
- `ralph-workflow/ralph/mcp/server/_saturated_dispatch.py:161-181`
  (Cause 5b)
- `ralph-workflow/ralph/mcp/server/runtime.py:867-950` (Cause 5c seam)

If any of those lines move, this doc must update before the fix the
doc references is merged. The §3b fix agents use this doc as a
target; an out-of-date citation is the same as a missing citation.
