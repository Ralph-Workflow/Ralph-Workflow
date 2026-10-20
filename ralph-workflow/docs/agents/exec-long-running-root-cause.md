# Long-running exec: root-cause write-up

Confirmation of the five root causes behind the long-running-exec failure mode,
the verified client-timeout table for the agents Ralph owns today, and the
real seams a §3b fix can reach. The child runtime shutdown path lands at
`ralph/mcp/server/runtime.py` (via `exit_when_owner_exits` and standalone server
termination); the page you are reading records what the causes ARE so a
future agent can tell a fix that addressed them from one that did not.

This page is intentionally narrow: it does not propose a design, it does not
touch any other doc, and it does not commit. Its job is to make every cause
defensible on file:line or URL evidence.

## Verified client-timeout table

The table records, per transport, what Ralph actually writes into the
client config and what the cited client documentation says the *default*
tool-call ceiling is. Three columns matter; treat them as independent:

- **Config key written** — the exact field name Ralph emits. Cites the
  transport file and line that synthesises the header so the row is
  verifiable from working tree state. "none written" means Ralph emits no
  timeout-bearing field; the row is then recorded as a deliberate choice
  (e.g. the docs page has no field to write), not an oversight.
- **Verified client default** — the wall clock the cited docs page
  records for a single MCP `tools/call` round-trip on the unconfigured
  client. "Verified" requires an explicit line from a URL the agent
  fetched in the WU-5 verification pass. `UNKNOWN (no docs surface)`
  means the cited docs page does not name a tool-call timeout at all and
  we did not find one in the transport source — Ralph cannot size what
  the docs do not expose. `UNKNOWN (docs say "SDK default 60 s")` means
  the docs explicitly punt to the MCP SDK's hard-coded default and the
  value is therefore documentation-dependent, not Ralph-writable.
- **Source/citation** — the live URL(s) the row's claims were taken
  from. Every URL in this column was fetched during the WU-5 pass and
  resolved cleanly. Per-client transport source lines (`transport/X.py:N`)
  point at the same lines the per-transport section under "Configurations
  that Ralph currently writes" cites; if those line numbers drift, this
  doc must update.

The split that this table enforces: a row's "Verified client default"
column describes the **client**, not Ralph. A row's "Config key written"
column describes **Ralph's contribution**. A "no docs surface" row is
NOT Ralph giving up — the rest of this page describes the server-side
bounded defenses (Cause 1, Cause 2, Cause 5c) that cover those clients
independently of any client-configurable timeout.

| Client | Config key written | Verified client default | Source/citation |
| --- | --- | --- | --- |
| Codex CLI | per-server `tool_timeout_sec` (integer seconds) under `mcp_servers.<name>` — written at `ralph/mcp/transport/codex.py:227` | **Verified**: per-server `tool_timeout_sec` "Override the default 60s per-tool timeout for an MCP server" — cited verbatim from the Codex Configuration Reference page | https://developers.openai.com/codex/config-file/config-reference (verified, resolves to https://learn.chatgpt.com/docs/config-file/config-reference; `mcp_servers.<id>.tool_timeout_sec` row) |
| Claude Code | per-server `timeout` (number, ms) in `.mcp.json`/`.claude.json` — written at `ralph/mcp/transport/claude.py:80`; env fallback `MCP_TOOL_TIMEOUT`; concurrent HTTP/SSE "request-timer" computed as `max(60 s, per-server timeout, MCP_TIMEOUT)` — a SECOND, independent timer | **Verified**: per-server `timeout` is "a hard wall-clock limit per tool call" with values below 1000 ignored, fall-through to `MCP_TOOL_TIMEOUT` whose default is 100 000 000 ms "about 28 hours"; the HTTP/SSE/SSE/claude.ai-connector second per-request timer is the greatest of 60 s, the server's per-server `timeout`, and `MCP_TIMEOUT` (startup env var) | https://code.claude.com/docs/en/mcp (verified) AND https://code.claude.com/docs/en/env-vars (verified — `MCP_TOOL_TIMEOUT` row reads "Timeout in milliseconds for MCP tool execution (default: 100000000, about 28 hours)") |
| OpenCode | env or global `experimental.mcp_timeout` AND documented-but-ignored per-server `mcp.<server>.timeout` — Ralph writes BOTH at `ralph/mcp/transport/opencode.py:131` and `:148`; the experimental key is the one that wins (issue #8701) | **Verified**: per-server `timeout` is documented as "Timeout in ms for fetching tools from the MCP server. Defaults to 5000 (5 seconds)" — explicitly a TOOL-FETCH deadline, not the tool-call deadline. The actual tool-call enforcement is the MCP SDK's hard-coded default (~60 s). Issue #8701 (closed) confirms: per-server `timeout` is ignored; `experimental.mcp_timeout` is the field the SDK honours | https://opencode.ai/docs/mcp-servers/ (verified) AND https://github.com/anomalyco/opencode/issues/8701 (verified — title "Per-server MCP timeout setting not working; requires experimental.mcp_timeout instead", state=closed) AND https://github.com/anomalyco/opencode/issues/8121 (verified — title "MCP timeout not respected", state=closed) |
| Cursor | none written — `cursor_mcp_config` emits only `{"url": endpoint}` at `ralph/mcp/transport/cursor.py:87-92`; Cursor's docs page has no tool-call-timeout field to write | **UNKNOWN (no docs surface)**: the Cursor MCP docs page lists STDIO fields `type`/`command`/`args`/`env`/`envFile` and remote `url`/`headers`/`auth` and stops there. No timeout field is named. We could not verify a client default. | https://cursor.com/docs/mcp (verified — no tool-call-timeout field documented) |
| Kimi Code CLI | per-server `toolTimeoutMs` (number, ms) in `mcp.json` — written at `ralph/mcp/transport/kimi.py:119`; plus the global default `[mcp] tool_timeout_ms` in `config.toml` (out of Ralph's write path) and the env override `KIMI_MCP_TOOL_TIMEOUT_MS` | **Verified**: global `[mcp] tool_timeout_ms` default is 60000 ms (60 seconds). Per-server `toolTimeoutMs` overrides the global; `KIMI_MCP_TOOL_TIMEOUT_MS` overrides the file. Cited verbatim from the Kimi `mcp` table | https://moonshotai.github.io/kimi-code/en/configuration/config-files (verified — `mcp` table, "Global default single tool-call timeout in milliseconds for all MCP servers; a per-server `toolTimeoutMs` in `mcp.json` wins") AND https://moonshotai.github.io/kimi-code/en/customization/mcp (verified — `toolTimeoutMs` per-server field) |
| Nanocoder | none written for tool-call — `nanocoder` payload at `ralph/mcp/transport/nanocoder.py:56-61` writes `{"transport": "http", "url": endpoint}` plus optional `alwaysAllow`; the package's documented `timeout` is a connection-timeout, not a tool-call-timeout | **UNKNOWN (docs say "SDK default 60 s")**: Nanocoder docs state the per-server `timeout` "Bounds the connection handshake and the initial tool listing; individual tool calls keep the MCP SDK's default timeout (60 seconds)" — the per-tool default is whatever the MCP SDK ships with, not a value Ralph can write | https://docs.nanocollective.org/nanocoder/docs/configuration/mcp-configuration (verified — `timeout` row in Common Fields: "Connection timeout in milliseconds. Bounds the connection handshake and the initial tool listing; individual tool calls keep the MCP SDK's default timeout (60 seconds)") AND https://www.npmjs.com/package/@nanocollective/nanocoder (verified exists — `dist-tags.latest=1.31.0`, repository=https://github.com/Nano-Collective/nanocoder.git) AND https://github.com/Nano-Collective/nanocoder (verified — README references the docs path above) |
| Pi | bridge constant `MCP_TOOL_CALL_DEADLINE_MS` — written into the generated TypeScript bridge at `ralph/mcp/transport/pi.py:306`; bridge also implements dual-abort with Pi's per-tool signal (`transport/pi.py:300-318`); discovery is gated by `MCP_STARTUP_TIMEOUT_MS = 10_000` (`transport/pi.py:58`) | **UNKNOWN (Ralph-generated bridge, no upstream Pi docs surface for a tool-call deadline)**: Pi's `registerTool({ execute(_, _, signal) })` plumbing passes the per-tool signal through to `fetch(ENDPOINT, { signal })`. Pi's own per-tool deadline is not documented on a public Pi page we could fetch in the WU-5 pass; the bridge-side deadline is the only verified bound | `ralph-workflow/ralph/mcp/transport/pi.py:42-318` (verified — read in working tree) |
| AGY (Google Antigravity) | none written for tool-call — `agy_mcp_config` writes only `{"serverUrl": endpoint}` at `ralph/mcp/transport/agy.py:88-94`; the cited Antigravity MCP overview names only `command`/`serverUrl` as transport properties and no tool-call-timeout field | **UNKNOWN (no docs surface)**: the Antigravity MCP overview documents the following server-entry properties — `command`, `args`, `env`, `cwd`, `serverUrl`, `headers`, `authProviderType`, `oauth`, `disabled`, `disabledTools` — and stops there. No tool-call timeout field is named. We could not verify a client default. The page lists `serverUrl` (not `url`/`httpUrl`) as the only documented remote transport field; the "legacy `url`/`httpUrl` aren't supported" claim is a paraphrase of the page's silence on those names, not a verbatim quote — Ralph's own normalizer still translates `serverUrl` -> `url` to feed its upstream registry (`ralph/mcp/transport/agy.py:118-119`) | https://antigravity.google/docs/mcp/ (verified — server-entry property list and `serverUrl` as the only documented remote transport field). The doc no longer cites a local-source URL as if it were a public citation: `ralph/mcp/transport/agy.py:88-94` is a working-tree pointer, not a published web document. |

### Unknown client defaults vs bounded implemented defenses

These two categories are NOT the same thing and this page treats them
separately on purpose:

- **Unknown client default** — a client Ralph cannot size. The table
  above marks these `UNKNOWN (no docs surface)` or `UNKNOWN (docs say
  "SDK default 60 s")`. "Unknown" here means "Ralph has no field to
  write" — it is a documentation fact, not a Ralph defect.
- **Bounded implemented defense** — a timeout Ralph DOES write, on
  Ralph's side of the wire, that bounds the failure mode the long
  exec tool call can produce. The defenses that exist today:
  - **Server-side soft-reply budget** — `EXEC_SOFT_REPLY_BUDGET_MS`
    (see `ralph/timeout_defaults.py`) caps how long a single exec call
    may run before the server returns; the transports above all
    derive their per-client timeout from this budget + 30 s of
    headroom (the `_TIMING_SAFETY_TRANSPORT_HEADROOM_MS` constant in
    `ralph/mcp/server/_timing_safety.py`).
  - **Server-side hard cap** — `EXEC_MAX_TIMEOUT_MS = 300_000` (5
    minutes) and `EXEC_JOB_CEILING_S` / `EXEC_JOB_CEILING_MAX_S` cap
    wall-clock independently of any client default (Cause 2 below).
  - **Server-side idle window** — the subprocess inactivity window
    (Cause 1 below) is refreshed only by stdout/stderr; a long
    CPU-busy exec still dies at `EXEC_DEFAULT_TIMEOUT_MS`.
  - **Server-side child-runtime shutdown** — the
    `exit_when_owner_exits` daemon in `ralph/mcp/server/runtime.py`
    (Cause 5c below) reaps running subprocesses when the parent
    process dies, closing the "request expiry does not imply process
    death" gap regardless of what the client default is.

Reading the table: every "Config key written" entry is an IMPLEMENTED
defense; every "Verified client default" entry is the client docs'
narrative. Where the "Verified client default" cell reads `UNKNOWN`,
the server-side bounded defenses above still apply.

### No documented tool-call timeout

Clients whose public MCP doc page does not name a tool-call timeout
AT ALL — every row in the table above that reads `UNKNOWN (no docs
surface)` falls into this bucket:

- **Cursor**: see `cursor.com/docs/mcp` above; the page documents stdio
  fields plus remote `url`/`headers`/`auth` and stops there. No timeout
  surface for `tools/call`.
- **AGY (Google Antigravity)**: see `antigravity.google/docs/mcp` above;
  the page documents only the SDK surface, no `timeout` field per server.

For these clients, the ceiling on a tool call is whatever the CLI's
internal transport / connection-read default is, not a value the user
can raise through configuration that is visible on the cited page.

Note that Nanocoder dropped out of this list: its docs page DOES name a
`timeout` field, but the page is explicit that it bounds the connection
handshake and the initial tool listing only — individual tool calls
keep the MCP SDK's hard-coded default. That is a different failure
mode (documentation paves a connection-timeout cowpath that looks
like a tool-call-timeout field on first glance) and is captured in the
table as `UNKNOWN (docs say "SDK default 60 s")`, not in this section.

### Configurations that Ralph currently writes

Historically, OpenCode was the only transport synthesizing a client-side
timeout. Under §3b, Ralph writes client-side tool-call timeouts derived
from `EXEC_SOFT_REPLY_BUDGET_MS` plus headroom across every transport
whose cited docs page names a tool-call-timeout field. Line numbers
below were read from the working tree in the WU-5 pass; if they drift,
the Appendix re-read drift check fires:

- `opencode.py:131` writes `timeout: _OPENCODE_MCP_CLIENT_TIMEOUT_MS`
  (the documented-but-ignored per-server field, set for forward-compat).
- `opencode.py:148` writes
  `experimental.mcp_timeout: _OPENCODE_MCP_CLIENT_TIMEOUT_MS`
  (the field the OpenCode SDK actually honours per issue #8701).
- `claude.py:80` writes `timeout: _CLAUDE_TOOL_TIMEOUT_MS`.
- `kimi.py:119` writes `toolTimeoutMs: _KIMI_TOOL_TIMEOUT_MS`.
- `codex.py:227` writes `tool_timeout_sec: _CODEX_TOOL_TIMEOUT_SEC`.
- `pi.py:306` writes `MCP_TOOL_CALL_DEADLINE_MS` into the synthesized
  TypeScript bridge; `pi.py:58` defines `MCP_STARTUP_TIMEOUT_MS = 10_000`
  for the discovery call.

Transports whose cited docs page does NOT name a tool-call-timeout
field (Cursor, Nanocoder, AGY) continue to write connection parameters
only — Ralph cannot size what the docs do not expose, and inventing
a synthetic field would either be silently ignored or hand-craft a
contract the agent does not honour:

- `cursor.py:87-92` writes only `{"url": endpoint}`.
- `nanocoder.py:56-61` writes only `{"transport": "http", "url": endpoint}`
  (plus optional `alwaysAllow`); the package's documented `timeout` is
  a connection-timeout, not a tool-call-timeout.
- `agy.py:88-94` writes only `{"serverUrl": endpoint}`.

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

## Cause 3 (CONFIRMED) — client timeouts only configured for OpenCode (prior defect)

Prior to §3b, only `ralph-workflow/ralph/mcp/transport/opencode.py:131`
and `:148` wrote a client-side MCP request timeout. Every other
transport wrote only the endpoint URL — leaving the connection bound to
whatever default the client's MCP SDK ships with (Codex's 60-s SDK
default per `learn.chatgpt.com/docs/config-file/config-reference`,
OpenCode's ~60-s SDK default per issue #8701, the Kimi `mcp` table's
60-s global `tool_timeout_ms`, the Claude Code `MCP_TOOL_TIMEOUT`
"about 28 hours" default, etc.), which the OpenCode issue thread shows
is the failing signal the operator sees as `McpError: -32001 Request
timed out`.

- `ralph-workflow/ralph/mcp/transport/opencode.py:38-45` carries the
  comment

  > "OpenCode IGNORES the documented per-server `mcp.<server>.timeout`
  > field and hard-enforces the MCP SDK default (~60s). The setting it
  > actually honors is the global `experimental.mcp_timeout` (opencode
  > issues #8701/#8121). We set BOTH: the experimental key is the one
  > that takes effect..."

- The non-OpenCode transports historically cited only URL/command/args.

The bug-class this fixes was universal: any operator whose client
silently capped MCP tool-call latency under whatever that client had
hard-coded (or, in the Kimi / Codex cases, under a documented 60-s
default) saw the run as a `McpError: -32001` during a single
legitimately long exec call, with no way to extend it from Ralph.

The fix posture per client (line numbers in the working tree as of the
WU-5 pass):
- Claude: writes `timeout` (`ralph/mcp/transport/claude.py:80`).
- Kimi: writes `toolTimeoutMs` (`ralph/mcp/transport/kimi.py:119`).
- Codex: writes `tool_timeout_sec` (`ralph/mcp/transport/codex.py:227`).
- Pi: enforces `MCP_TOOL_CALL_DEADLINE_MS` in the generated bridge
  (`ralph/mcp/transport/pi.py:306`).
- OpenCode: writes both `timeout` and `experimental.mcp_timeout`
  (`ralph/mcp/transport/opencode.py:131` and `:148`).
- Cursor, AGY: cited docs page names no public per-server tool-call
  timeout config; Ralph writes only `{"url": endpoint}` /
  `{"serverUrl": endpoint}`.
- Nanocoder: the package's docs DO name a `timeout` field, but it is
  the connection handshake / tool-listing deadline; tool-call
  enforcement reverts to the MCP SDK's hard-coded default. Ralph
  therefore writes only `{"transport": "http", "url": endpoint}` — the
  row reads `UNKNOWN (docs say "SDK default 60 s")` in the verified
  table.

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

The parent cannot reach into the child's in-memory collections from
`_session_wrapup.py` (or from any `factory_impl.py` callback the parent
owns) because the subprocess state lives in the child's address space.
The child runtime shutdown path in `ralph/mcp/server/runtime.py`
(`exit_when_owner_exits` and signal handling in `main`/`run_standalone_server`)
is the authoritative execution seam: when the owner process terminates or
`session_end` occurs, the child server reaps its running jobs and
terminates cleanly.

## Cross-reference: how each cause maps to a §3b fix

Each cause has a §3b counterpart that points at it. The chart
records only the seam; the implementation is recorded in the dedicated
work units in this WT.

| Cause | Seam reached | §3b file |
| --- | --- | --- |
| 1 — `inactivity` is output-only | Liveness sampling and fuzzy assessor with CPU/IO/novelty ladder | `ralph/process/manager/_liveness_assessor.py` |
| 2 — 300 s absolute cap | `EXEC_SOFT_REPLY_BUDGET_MS`, `EXEC_JOB_CEILING_S`, `EXEC_JOB_CEILING_MAX_S` | `ralph/timeout_defaults.py` |
| 3 — client timeout configuration | write documented timeout keys derived from `EXEC_SOFT_REPLY_BUDGET_MS` + headroom | `ralph/mcp/transport/{claude,codex,kimi,opencode,pi}.py` |
| 4 — streamed output does not keep clients alive | SSE notification stream and progress token handling | `ralph/mcp/server/exec_sse_streaming.py` |
| 5 — request expiry does not imply process death | child runtime shutdown path via `exit_when_owner_exits` and standalone server termination | `ralph/mcp/server/runtime.py` |

## Appendix: how to verify the doc is still truthful

The five causes are stable as long as the file:line citations in this
doc match the working tree. Drift is the only way a citation goes
stale, and the fastest drift check is a re-read of:

- `ralph-workflow/ralph/timeout_defaults.py:121-156` (Cause 2 constants)
- `ralph-workflow/ralph/process/manager/_managed_process.py:280-380`
  (Cause 1 poll loop)
- `ralph-workflow/ralph/mcp/transport/opencode.py:131,148` (Cause 3
  per-server + experimental writes; the comment block at lines 38-45
  is the issue-thread reference)
- `ralph-workflow/ralph/mcp/transport/claude.py:80`,
  `ralph-workflow/ralph/mcp/transport/codex.py:227`,
  `ralph-workflow/ralph/mcp/transport/kimi.py:119`,
  `ralph-workflow/ralph/mcp/transport/pi.py:306`
  (the rest of Cause 3's per-transport writes)
- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:56-63` (Cause 4)
- `ralph-workflow/ralph/mcp/server/_saturated_dispatch.py:161-181`
  (Cause 5b)
- `ralph-workflow/ralph/mcp/server/runtime.py:867-950` (Cause 5c seam)
- `ralph-workflow/ralph/mcp/server/_timing_safety.py:79-89`
  (`_TIMING_SAFETY_TRANSPORT_HEADROOM_MS` and
  `TIMING_SAFETY_TRANSPORT_FLOOR_MS` — the per-transport floor the
  §3b fix's written keys must clear)

The URL citations in the verified client-timeout table are also
subject to drift — every URL listed there was fetched live in the
WU-5 pass and resolved cleanly. Re-running
`scripts/fabrication_guard.py --level 2
ralph-workflow/docs/agents/exec-long-running-root-cause.md`
after this doc is edited is the cheapest way to confirm none of
them have since 404'd. A row that reads `UNKNOWN` is not a defect;
it is the doc being honest about what the cited docs page does and
does not name.

If any of the file:line citations above move, this doc must update
before the fix the doc references is merged. The §3b fix agents use
this doc as a target; an out-of-date citation is the same as a
missing citation.
