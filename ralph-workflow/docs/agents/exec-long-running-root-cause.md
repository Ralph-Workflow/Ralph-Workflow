# Long-running exec: root-cause write-up

The five root causes behind the long-running-exec failure mode, the verified
client-timeout table for the agents Ralph owns today, and the seam each cause
maps to. The child runtime shutdown path lands at
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
  client. "Verified" requires an explicit line from a URL an agent
  fetched live, with every URL re-opened and the quoted string read back
  off the page. `UNKNOWN (no docs surface)`
  means the cited docs page does not name a tool-call timeout at all and
  we did not find one in the transport source — Ralph cannot size what
  the docs do not expose. `UNKNOWN (docs say "SDK default 60 s")` means
  the docs explicitly punt to the MCP SDK's hard-coded default and the
  value is therefore documentation-dependent, not Ralph-writable.
- **Source/citation** — the live URL(s) the row's claims were taken
  from. Every URL in this column was fetched live and the quoted string
  read back off the page; all of them still resolve and all of them
  still carry the quoted text. Per-client transport
  source lines (`transport/X.py:N`)
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
| Codex CLI | per-server `tool_timeout_sec` (integer seconds) under `mcp_servers.<name>` — written at `ralph/mcp/transport/codex.py:127` | **Verified**: per-server `tool_timeout_sec` "Override the default 60s per-tool timeout for an MCP server" — cited verbatim from the Codex Configuration Reference page | https://developers.openai.com/codex/config-file/config-reference (verified, resolves to https://learn.chatgpt.com/docs/config-file/config-reference; `mcp_servers.<id>.tool_timeout_sec` row) |
| Claude Code | per-server `timeout` (number, ms) in the server's `.mcp.json` entry — written into the synthesized `--mcp-config` payload at `ralph/mcp/transport/claude.py:49`; env fallback `MCP_TOOL_TIMEOUT`; for an HTTP, SSE, or claude.ai-connector server a SECOND, independent per-request timer set to the greatest of 60 s, that server's per-server `timeout`, and `MCP_TIMEOUT` | **Verified**: per-server `timeout` is "a hard wall-clock limit per tool call"; "Values below 1000 are ignored and fall through to MCP_TOOL_TIMEOUT, or to its default of about 28 hours when that variable is unset"; the second per-request timer is "the greatest of three values: 60 seconds, the tool timeout that applies to the server, and MCP_TIMEOUT". A THIRD documented clock, `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`, "abort[s] with an error instead of waiting for the overall MCP_TOOL_TIMEOUT" when a server "sends no response and no progress notification" for that long, and "Overrides the per-transport defaults of 300000 (5 minutes) for network servers and 1800000 (30 minutes) for stdio servers". The 75 000 ms Ralph writes clears every documented floor: above 60000 so it raises the 60-second per-request limit, and at least 1000 so "a per-server timeout of at least 1000 also sets the minimum idle window for that server's tool calls" | https://code.claude.com/docs/en/mcp (verified) AND https://code.claude.com/docs/en/env-vars (verified — `MCP_TOOL_TIMEOUT` row reads "Timeout in milliseconds for MCP tool execution (default: 100000000, about 28 hours)"; the `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` row supplies the idle defaults) |
| OpenCode | env or global `experimental.mcp_timeout` AND documented-but-ignored per-server `mcp.<server>.timeout` — Ralph writes BOTH at `ralph/mcp/transport/opencode.py:77` and `:91`; the experimental key is the one that wins (issue #8701) | **Verified**: per-server `timeout` is documented as "Timeout in ms for fetching tools from the MCP server. Defaults to 5000 (5 seconds)" — explicitly a TOOL-FETCH deadline, not the tool-call deadline. The actual tool-call enforcement is the MCP SDK's hard-coded default (~60 s). Issue #8701 (closed) confirms: per-server `timeout` is ignored for tool calls; `experimental.mcp_timeout` is the field the SDK honours. **Schema drift — no Ralph-side write required:** the machine-readable schema Ralph stamps into its own generated config (`$schema` written at `ralph/mcp/transport/opencode.py:116`) marks `experimental.mcp_timeout` `"deprecated": true` and adds a non-deprecated `timeout` OBJECT with `startup` / `catalog` / `execution` sub-keys, at both `mcp.timeout.*` (global) and per-server `mcp.<server>.timeout.*`. The human-facing MCP docs page documents only the scalar per-server `timeout`, so the docs trail the schema. Ralph does not write `timeout.execution`: OpenCode's own normalizer supplies it, on four independently cited grounds. **(1)** `execution` is the real tool-call wall — it is the sub-key the MCP client resolves and hands to the tool-call deadline, and OpenCode's own default for it is `12 * 60 * 60 * 1_000 // 12 hours`, so the tool-call ceiling is hours. **(2)** OpenCode translates Ralph's deprecated key onto the superseding key itself: the config normalizer reads `experimental.mcp_timeout` and assigns that one value to BOTH `timeout.catalog` and `timeout.execution`, so the field Ralph already writes at `ralph/mcp/transport/opencode.py:91` lands on `timeout.execution` without Ralph emitting it. **(3)** Ralph's per-server scalar already expands into the object form: every legacy `mcp.<server>` entry is routed through `migrateMcp`, whose `migrateMcpTimeout` returns `{ startup: timeout, catalog: timeout, execution: timeout }`, so Ralph's 330000 ms at `ralph/mcp/transport/opencode.py:77` sets `execution` to 330000 ms as well. **(4)** The `execution` sub-key is absent from the `Timeout` object of the OpenCode release cited below (`v1.18.25`): it models the `Timeout` object as `{ startup, request }`, with `request` described as "Maximum time in milliseconds to wait for each MCP request after initialization". The global `mcp.timeout.execution` key is a FILL-IN default merged per server as `{ ...timeout, ...server.timeout }`, so a global `execution` of 330000 would pull every operator-declared MCP server's tool calls down from OpenCode's 12-hour default to ~5.5 minutes. Ralph's writes are sufficient and depend on no field on a deprecation path | https://opencode.ai/docs/mcp-servers/ (verified) AND https://github.com/anomalyco/opencode/issues/8701 (verified — title "Per-server MCP timeout setting not working; requires experimental.mcp_timeout instead", state=closed) AND https://github.com/anomalyco/opencode/issues/8121 (verified — title "MCP timeout not respected", state=closed) AND https://opencode.ai/config.json (verified — the published config schema both `experimental.mcp_timeout` and the `timeout.execution` object are read from) AND https://github.com/anomalyco/opencode/blob/dev/packages/core/src/config/normalize.ts (verified — `normalizeMcp` copies `experimental.mcp_timeout` into BOTH `timeout.catalog` and `timeout.execution`, and routes legacy `mcp.<server>` entries through `migrateMcp`) AND https://github.com/anomalyco/opencode/blob/dev/packages/core/src/v1/config/migrate.ts (verified — `migrateMcpTimeout` returns `{ startup: timeout, catalog: timeout, execution: timeout }`) AND https://github.com/anomalyco/opencode/blob/dev/packages/core/src/mcp/client.ts (verified — `execution` is the resolved value passed to the `client.callTool(...)` deadline, defaulting to `12 * 60 * 60 * 1_000`) AND https://github.com/anomalyco/opencode/blob/dev/packages/core/src/config/plugin/mcp.ts (verified — the global `mcp.timeout` is a fill-in default merged per server as `{ ...timeout, ...server.timeout }`) AND https://github.com/anomalyco/opencode/blob/v1.18.25/packages/core/src/config/mcp.ts (verified — `Timeout` is `{ startup, request }`; no `execution` sub-key) |
| Cursor | none written — `cursor_mcp_config` emits only `{"url": endpoint}` at `ralph/mcp/transport/cursor.py:28`; Cursor's docs page has no tool-call-timeout field to write | **UNKNOWN (no docs surface)**: the Cursor MCP docs page lists STDIO fields `type`/`command`/`args`/`env`/`envFile` and remote `url`/`headers`/`auth` and stops there. No timeout field is named. We could not verify a client default. | https://cursor.com/docs/mcp (verified — no tool-call-timeout field documented) |
| Kimi Code CLI | per-server `toolTimeoutMs` (number, ms) in `mcp.json` — written at `ralph/mcp/transport/kimi.py:34`; plus the global default `[mcp] tool_timeout_ms` in `config.toml` (out of Ralph's write path) and the env override `KIMI_MCP_TOOL_TIMEOUT_MS` | **Verified**: global `[mcp] tool_timeout_ms` default is 60000 ms (60 seconds). Per-server `toolTimeoutMs` overrides the global; `KIMI_MCP_TOOL_TIMEOUT_MS` overrides the file. Cited verbatim from the Kimi `mcp` table | https://moonshotai.github.io/kimi-code/en/configuration/config-files (verified — `mcp` table, "Global default single tool-call timeout in milliseconds for all MCP servers; a per-server `toolTimeoutMs` in `mcp.json` wins") AND https://moonshotai.github.io/kimi-code/en/customization/mcp (verified — `toolTimeoutMs` per-server field) |
| Nanocoder | none written for tool-call — `nanocoder` payload at `ralph/mcp/transport/nanocoder.py:36-37` writes `{"transport": "http", "url": endpoint}` plus optional `alwaysAllow`; the package's documented `timeout` is a connection-timeout, not a tool-call-timeout | **UNKNOWN (docs say "SDK default 60 s")**: Nanocoder docs state the per-server `timeout` "Bounds the connection handshake and the initial tool listing; individual tool calls keep the MCP SDK's default timeout (60 seconds)" — the per-tool default is whatever the MCP SDK ships with, not a value Ralph can write | https://docs.nanocollective.org/nanocoder/docs/v1.31.0/configuration/mcp-configuration/ (verified — `timeout` row in Common Fields: "Connection timeout in milliseconds. Bounds the connection handshake and the initial tool listing; individual tool calls keep the MCP SDK's default timeout (60 seconds)") AND https://registry.npmjs.org/@nanocollective/nanocoder (verified — `dist-tags.latest=1.31.0`; `repository` points at the GitHub repo below) AND https://github.com/Nano-Collective/nanocoder (verified — repo exists; README states "Full documentation is available online at docs.nanocollective.org") |
| Pi | bridge constant `MCP_TOOL_CALL_DEADLINE_MS` — written into the generated TypeScript bridge at `ralph/mcp/transport/pi.py:59`; bridge also implements dual-abort with Pi's per-tool signal (`transport/pi.py:272-293`); discovery is gated by `MCP_STARTUP_TIMEOUT_MS = 10_000` (`transport/pi.py:58`) | **PARTIALLY verified — Pi documents a 60-second per-request timeout, but not on the path Ralph uses.** Pi's MCP docs state that both server types support "`timeout`: per-request timeout in seconds (default 60). Progress notifications reset it." That `timeout` configures **Pi's own MCP client** (`mcpServers` entries in `~/.pi/agent/mcp.json` / `.pi/mcp.json`, or `pi.registerMcpServer`), and Ralph's transport does NOT go through it: Ralph generates a Pi *extension* that registers Pi custom tools and issues a direct `fetch` to Ralph's own HTTP MCP endpoint (`fetch(ENDPOINT, { signal })`, per the generated bridge at `ralph/mcp/transport/pi.py:173`). Pi's 60-second default therefore never arms on Ralph's path. The only Pi-side clock that reaches the bridge is the per-tool `AbortSignal` that `registerTool({ execute(_, _, signal) })` passes down, and **the duration of that signal is not stated on any cited Pi page** — that residue, not "no docs surface at all", is what this row's `UNKNOWN` covers, so the bridge-side deadline remains the only verified bound. Note also that Pi's own two pages disagree on the unit: the `registerMcpServer` reference lists `timeout` (number) as "Timeout in milliseconds" while the MCP page says seconds; this row therefore quotes the MCP page's default and does not assert a unit for the API-reference field | https://pi.dev/docs/latest/mcp (verified — "`timeout`: per-request timeout in seconds (default 60). Progress notifications reset it.") AND https://pi.dev/docs/latest/extensions (verified — `pi.registerMcpServer` config field list, `timeout` (number) "Timeout in milliseconds") AND `ralph-workflow/ralph/mcp/transport/pi.py:15-305` (verified — read in working tree; the bridge's own `fetch` call is at `:173`) |
| AGY (Google Antigravity) | none written for tool-call — `agy_mcp_config` writes only `{"serverUrl": endpoint}` at `ralph/mcp/transport/agy.py:27`; the cited Antigravity MCP overview names only `command`/`serverUrl` as transport properties and no tool-call-timeout field | **UNKNOWN (no docs surface)**: the Antigravity MCP overview documents the following server-entry properties — `command`, `args`, `env`, `cwd`, `serverUrl`, `headers`, `authProviderType`, `oauth`, `disabled`, `disabledTools` — and stops there. No tool-call timeout field is named. We could not verify a client default. The page lists `serverUrl` (not `url`/`httpUrl`) as the only documented remote transport field; the "legacy `url`/`httpUrl` aren't supported" claim is a paraphrase of the page's silence on those names, not a verbatim quote — Ralph's own normalizer still translates `serverUrl` -> `url` to feed its upstream registry (`ralph/mcp/transport/agy.py:40-41`) | https://antigravity.google/docs/mcp/ (verified — server-entry property list and `serverUrl` as the only documented remote transport field). `ralph/mcp/transport/agy.py:27` is a working-tree pointer, not a published web document. |

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
    (`ralph/timeout_defaults.py:169`) caps how long a single exec call
    may run before the server returns; the transports above derive
    their per-client timeout from this budget + 30 s of headroom
    (the `TIMING_SAFETY_TRANSPORT_HEADROOM_MS` constant at
    `ralph/mcp/server/_timing_safety.py:35`); OpenCode is the one
    exception — `ralph/mcp/transport/opencode.py:26-28` sizes it as
    `max(EXEC_MAX_TIMEOUT_MS, EXEC_SOFT_REPLY_BUDGET_MS) + 30 s` =
    330 s.
  - **Server-side hard cap** — `EXEC_MAX_TIMEOUT_MS = 300_000` (5
    minutes, `ralph/timeout_defaults.py:39`) bounds the
    injected-runner seam, and `EXEC_JOB_CEILING_S` /
    `EXEC_JOB_CEILING_MAX_S` (`ralph/timeout_defaults.py:171` and
    `:173`) cap production job wall-clock independently of any client
    default (Cause 2 below).
  - **Server-side child-runtime shutdown** — the
    `exit_when_owner_exits` daemon in `ralph/mcp/server/runtime.py`
    (`:625-649`) reaps running subprocesses when the parent
    process dies, closing the "request expiry does not imply process
    death" gap regardless of what the client default is.
  - **Server-side liveness-based kill** — under the job and liveness
    architecture, a fuzzy liveness assessor
    (`ralph/process/manager/_liveness_assessor.py`) sits over the long-running
    job flow. A command that stays
    silent on stdout but is busy on CPU, disk I/O, child-process activity,
    or voluntary context switches lives as long as its
    liveness score stays above the stall-pressure kill line, up to the
    registry's `hard_cap` ceiling (`EXEC_JOB_CEILING_MAX_S = 4 h`).
    See "Cause 1 — production liveness subsystem (CURRENT)" further on
    for the rule table.

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

Note the Nanocoder row: its docs page DOES name a
`timeout` field, but the page is explicit that it bounds the connection
handshake and the initial tool listing only — individual tool calls
keep the MCP SDK's hard-coded default. That is a different failure
mode (documentation paves a connection-timeout cowpath that looks
like a tool-call-timeout field on first glance) and is captured in the
table as `UNKNOWN (docs say "SDK default 60 s")`, not in this section.

### Configurations that Ralph currently writes

Ralph writes client-side tool-call timeouts derived
from `EXEC_SOFT_REPLY_BUDGET_MS` plus headroom across every transport
whose cited docs page names a tool-call-timeout field. The line numbers
below are read from the working tree; if they drift, the Appendix
re-read drift check fires:

- `opencode.py:77` writes `timeout: _OPENCODE_MCP_CLIENT_TIMEOUT_MS`
  (the documented-but-ignored per-server field, set for forward-compat).
- `opencode.py:91` writes
  `experimental.mcp_timeout: _OPENCODE_MCP_CLIENT_TIMEOUT_MS`
  (the field the OpenCode SDK actually honours per issue #8701).
- `claude.py:49` writes `timeout: _CLAUDE_TOOL_TIMEOUT_MS`.
- `kimi.py:34` writes `toolTimeoutMs: _KIMI_TOOL_TIMEOUT_MS`.
- `codex.py:127` writes `tool_timeout_sec: _CODEX_TOOL_TIMEOUT_SEC`.
- `pi.py:59` writes `MCP_TOOL_CALL_DEADLINE_MS` into the synthesized
  TypeScript bridge; `pi.py:58` defines `MCP_STARTUP_TIMEOUT_MS = 10_000`
  for the discovery call.

Transports whose cited docs page does NOT name a tool-call-timeout
field (Cursor, Nanocoder, AGY) continue to write connection parameters
only — Ralph cannot size what the docs do not expose, and inventing
a synthetic field would either be silently ignored or hand-craft a
contract the agent does not honour:

- `cursor.py:28` writes only `{"url": endpoint}`.
- `nanocoder.py:36-37` writes only `{"transport": "http", "url": endpoint}`
  (plus optional `alwaysAllow`); the package's documented `timeout` is
  a connection-timeout, not a tool-call-timeout.
- `agy.py:27` writes only `{"serverUrl": endpoint}`.

## Cause 1 (CONFIRMED) — `inactivity` is output-only (HISTORICAL)

The output-only inactivity window is the timeout rule of the
**injected-runner seam**: it is refreshed only by a chunk from
the child's stdout/stderr, never by CPU work. The production entry path
does not use it — every normal `exec` becomes an `ExecJobRegistry` job
(`ralph/mcp/tools/exec.py:572-607`) whose kills come from sampled
liveness. See "Cause 1 — production liveness subsystem (CURRENT)" further
on for that rule.

- `ralph-workflow/ralph/process/manager/_managed_process.py:279` resets
  `last_output_at_ref[0] = _time.monotonic()` inside `_read_output_stream`
  only when a non-empty `chunk` arrives from the child's `stdout`/`stderr`.
- The poll loop at `ralph-workflow/ralph/process/manager/_managed_process.py:344-360`
  computes `inactivity_deadline = last_output_at_ref[0] + timeout`
  (`:344-346`) and arms the kill with `timeout_cause = "inactivity"` and
  `timeout_value = timeout` (`:354-356`); the terminate-then-raise is at
  `:357-360`.
- `EXEC_DEFAULT_TIMEOUT_MS = 90_000` (`ralph-workflow/ralph/timeout_defaults.py:37`)
  — a 90-second default inactivity cap. A CPU-busy command that never
  emits stdout (e.g. tight loop, in-process buffered logging, redirected
  output to a file) dies at `timeout_ms` with cause `inactivity` and
  the structured error template `hard_cap=False` / `inactivity=True`
  prompts the agent to "raise `timeout_ms` up to the cap" — but raising
  it only widens the same output-only window.

The seam that applies this rule is `ralph-workflow/ralph/mcp/tools/exec.py:558-571`,
which the production entry path does not take.

### Cause 1 — production liveness subsystem (CURRENT)

Under the job and liveness architecture, a fuzzy liveness
assessor sits over the exec job flow. The output-only inactivity window
is not the kill rule for any production `exec` call, short or long: the
production path always starts a registry job and the registry decides kills
from sampled liveness.

- ralph-workflow/ralph/mcp/tools/exec.py:572-607 is the production entry
  path. It resolves the registry (`:573-580`), calls
  `effective_registry.start(...)` unconditionally (`:590-597`), and then
  `effective_registry.wait(job_id, wait_ms=EXEC_SOFT_REPLY_BUDGET_MS, ...)`
  (`:598-603`). **Every** normal `exec` therefore becomes a registry job;
  none of them fall back to the output-only window.
- ralph-workflow/ralph/mcp/tools/exec.py:558-571 is the **injected runner
  seam**, not a fallback for "short" calls. It is entered only when
  `effective_deps.runner is not None` or `run_command is not
  _DEFAULT_RUN_COMMAND` (`:558-560`) — i.e. when a caller injected a custom
  runner. Only on this seam does `run_command` reach
  `_run_subprocess` (`ralph/mcp/tools/exec.py:445`) and the
  `_managed_process.py` output-only inactivity window.
- A short in-request completion returns the legacy output shape
  end-to-end — `exec.py:645-652` converts a terminal `JobResult` into the
  same `_CompletedProcessAdapter` the seam produced — so the *observable*
  output contract is the same on both paths. The kill rule and the
  response shape are separate contracts.
- `ralph-workflow/ralph/process/manager/_liveness_assessor.py` —
  Sugeno weighted-average over six working signals (`o`, `c`,
  `io`, `t`, `n`, `w`; the per-signal helpers are at `:78-155` and the
  R1..R7 rule weights at `:157-158`); the verdict score is integrated by
  `StallIntegrator` (class at `:303`, `step` method at `:312`, leaky `threshold_(theta - s)/theta`) and a
  sustained-suspicion clock (`SuspicionClock`, imported from
  `ralph/process/manager/_suspicion_clock.py` at `:8` and re-exported at
  `:15`) that tracks R4/R7 streaks (`_SUSPICIOUS_RULES` at `:159`) for
  `EXEC_NO_PROGRESS_WINDOW_S` (15 min, `timeout_defaults.py:167`) →
  `no_progress`.
  Per-spec rule table (`:157`, `:202`): R1 z=1.0, R2 z=0.9, R3 z=0.9,
  R4 z=0.6, R5 z=0.0, R6 z=0.2 (fire-only, see docstring), R7 z=0.55.
- `ralph-workflow/ralph/process/manager/_liveness_sampler.py` —
  psutil-tree sampler (`TreeSampler` at `_liveness_sampler.py:238`) reads the process tree every
  `EXEC_LIVENESS_SAMPLE_INTERVAL_S` (2 s, `timeout_defaults.py:137`) over an
  `EXEC_LIVENESS_EVIDENCE_WINDOW_S` (30 s, `timeout_defaults.py:139`) window.
  Each signal is independently `None`-on-read-failure (D5 fail-closed) and the
  `_windowed_rate` rebaseline lives at `_liveness_sampler.py:182`; the typed
  adapter is local to the sampler because the existing
  `_PsutilProcessLike` protocol
  (`ralph/process/manager/_process_manager_types.py:27-38`) lacks
  `cpu_times`/`io_counters`/`num_ctx_switches`.
- `ralph-workflow/ralph/mcp/tools/_exec_jobs.py` — `ExecJobRegistry`
  (`class` at `:238`) runs the job on a sample thread that drives the
  assessor every cycle, accumulates stall pressure, and decides the kill
  cause. `KillCause` (`:52-54`) names six literals, but only five are
  actually emitted by the registry today: `abandoned` (`:445`), `hard_cap`
  (`:449`), `stalled` (`:484`), `no_progress` (`:488`), and `session_end`
  (`:722`, the `shutdown_all` default). `restart_lost` is declared and
  rendered by the error formatter
  (`ralph/mcp/tools/_exec_execution_error.py:15`) but has no
  producer in `_exec_jobs.py`: a restart destroys the in-memory registry, so
  the only observable restart contract is the non-retryable
  "unknown or expired job; process was killed or lost in server restart"
  error from `wait()`/`cancel()`
  (the literal at `_exec_jobs.py:653`; `_lookup_job_locked` is the only
  site that holds the message string — `_exec_jobs.py:631-654`,
  with the `_build_job_result` consumer at `_exec_jobs.py:375-405`),
  which is covered by
  `tests/test_exec_jobs_session_lifecycle.py::test_restart_lost_fresh_registry_returns_nonretryable_lost_in_server_restart_error`
  (`tests/test_exec_jobs_session_lifecycle.py:47`, invented-id assertion at `:63-68`)
  and `tests/test_tool_exec_wait_cancel.py::test_exec_wait_for_restart_lost_job_id_returns_idempotent_nonretryable_error`.
  A short in-request completion lands in the registry as a regular
  `JobResult` and the legacy output shape is preserved end-to-end;
  only jobs that take longer than the soft-reply budget return a
  snapshot with a `job_id` and the "call exec_wait; do not re-run"
  instruction (`exec.py:609-621`, `_exec_jobs.py:772-787`).

## Cause 2 (CONFIRMED) — single 300-second absolute cap

Even after the operator raises `timeout_ms`, the wall clock is capped.

- `EXEC_MAX_TIMEOUT_MS = 300_000` (`ralph-workflow/ralph/timeout_defaults.py:39`)
  — 5 minutes. Larger values are rejected at the import-time invariant
  check below the constant.
- On the **injected-runner seam** only,
  `ralph-workflow/ralph/mcp/tools/exec.py:473-478` passes
  `hard_timeout=EXEC_MAX_TIMEOUT_MS / 1000` to
  `handle.communicate_and_cleanup`. The `inactivity` window is gated BELOW
  this; the `hard_cap` window is the outermost envelope. Note this is the
  seam path (`exec.py:558-571` → `exec.py:445` → `exec.py:473`), not the
  production entry path.
- On the **production entry path**, the wall-clock cap is the registry's
  job ceiling, not `EXEC_MAX_TIMEOUT_MS`:
  `ralph-workflow/ralph/mcp/tools/_exec_jobs.py:194-198` resolves the
  ceiling once per job (`ceiling_s = min(max_runtime_ms / 1000.0,
  EXEC_JOB_CEILING_MAX_S)` when the caller set `max_runtime_ms`, else
  `EXEC_JOB_CEILING_S` (`ralph/timeout_defaults.py:171`, 3600 s)) and stores
  it as `job.ceiling_ms`; the registry kills with `cause="hard_cap"` at
  `_exec_jobs.py:449` against that single value. `EXEC_JOB_CEILING_MAX_S = 14400.0`
  (`ralph/timeout_defaults.py:173`, 4 h) is the outer bound.
- **Hard-cap error text.** `ralph-workflow/ralph/mcp/tools/_exec_execution_error.py`
  `ExecutionError._render_timeout` (`:108-118`, split into a job path and
  a legacy in-request path) renders `timeout_cause == "hard_cap"` with the
  ceiling that actually bound the run: a job `hard_cap` renders
  `_exec_execution_error.py:202-213` ("Killed at the {ceiling}ms job runtime
  ceiling… raise `max_runtime_ms` instead") and the silence-only stall
  sentence appears only on the legacy in-request path
  (`_exec_execution_error.py:223-235`).
- **Background path.** Every exec-family tool (`exec`, `unsafe_exec`, the
  iteration-timeboxed variants) reaches the server's background execution
  path: `exec_wait` and `exec_cancel` are registered tools
  (`ralph/mcp/tools/names.py:36-37`, exported at `:95-96`; handlers in
  `ralph/mcp/tools/_exec_job_tools.py:41-42` and `handle_exec_wait` at
  `:96`). A long call returns a `JobSnapshot` carrying a `job_id` and
  the `"instruction": "call exec_wait; do not re-run"` string
  (`ralph/mcp/tools/exec.py:609-621`), and `handle_exec_wait` clamps its
  `wait_ms` to `EXEC_SOFT_REPLY_BUDGET_MS`
  (`_exec_job_tools.py:108-112`). The hard-cap template points at
  `exec_wait` and `exec_cancel`.

## Cause 3 (CONFIRMED) — client-side timeouts on every transport that documents one

A client that silently caps MCP tool-call latency under whatever that client
hard-codes — or, in the Kimi / Codex cases, under a documented 60-s
default — surfaces the run as
`McpError: MCP error -32001: Request timed out` during a single
legitimately long exec call, with no way to extend it from Ralph. That is
the failing signal the OpenCode issue thread shows, as the verbatim error
string quoted in issue #8701's body. The SDK defaults behind those failures are
Codex's 60-s default per https://learn.chatgpt.com/docs/config-file/config-reference,
OpenCode's ~60-s default per issue #8701, the Kimi `mcp` table's
60-s global `tool_timeout_ms`, and the Claude Code `MCP_TOOL_TIMEOUT`
"about 28 hours" default.

Ralph writes a client-side MCP request timeout on every transport whose
cited docs page names a tool-call-timeout field.

- `ralph-workflow/ralph/mcp/transport/opencode.py` writes both timeout
  fields from one constant, `_OPENCODE_MCP_CLIENT_TIMEOUT_MS` (`:26-28`),
  sized as `max(EXEC_MAX_TIMEOUT_MS, EXEC_SOFT_REPLY_BUDGET_MS)` plus the
  `_OPENCODE_MCP_CLIENT_TIMEOUT_HEADROOM_MS` headroom (`:24`). The
  per-server `timeout` field is written at `:77` and the global
  `experimental.mcp_timeout` field at `:91`. OpenCode ignores the
  per-server `mcp.<server>.timeout` field for tool calls and enforces the
  MCP SDK default (~60 s) instead; `experimental.mcp_timeout` is the field
  it honours (opencode issues #8701/#8121).

The written keys per client:
- Claude: writes `timeout` (`ralph/mcp/transport/claude.py:49`).
- Kimi: writes `toolTimeoutMs` (`ralph/mcp/transport/kimi.py:34`).
- Codex: writes `tool_timeout_sec` (`ralph/mcp/transport/codex.py:127`).
- Pi: enforces `MCP_TOOL_CALL_DEADLINE_MS` in the generated bridge
  (`ralph/mcp/transport/pi.py:19`, written into the bridge at `:59`).
- OpenCode: writes both `timeout` and `experimental.mcp_timeout`
  (`ralph/mcp/transport/opencode.py:77` and `:91`).
- Cursor, AGY: cited docs page names no public per-server tool-call
  timeout config; Ralph writes only `{"url": endpoint}` /
  `{"serverUrl": endpoint}` (`cursor.py:28`, `agy.py:27`).
- Nanocoder: the package's docs DO name a `timeout` field, but it is
  the connection handshake / tool-listing deadline; tool-call
  enforcement reverts to the MCP SDK's hard-coded default. Ralph
  therefore writes only `{"transport": "http", "url": endpoint}`
  (`nanocoder.py:36-37`) — the row reads `UNKNOWN (docs say "SDK default
  60 s")` in the verified table.
- OpenCode, schema drift (no Ralph-side write; see the
  verified table's OpenCode row): the published OpenCode config schema
  marks `experimental.mcp_timeout` deprecated and exposes a
  non-deprecated `timeout.execution` at both `mcp.timeout.*` and
  `mcp.<server>.timeout.*`, and Ralph writes neither directly.
  OpenCode's own config normalizer copies
  `experimental.mcp_timeout` into BOTH `timeout.catalog` and
  `timeout.execution`, and expands Ralph's per-server scalar through
  `migrateMcpTimeout` into `{ startup, catalog, execution }`, so the
  330000 ms Ralph writes is already the value OpenCode enforces on the
  tool-call deadline. The global `mcp.timeout` key is a fill-in default
  merged per server as `{ ...timeout, ...server.timeout }`, so a global
  `execution` would cap every operator-declared MCP server at ~5.5
  minutes instead of OpenCode's 12-hour default. Citations: the verified
  table's OpenCode row, https://opencode.ai/config.json, and
  https://github.com/anomalyco/opencode/blob/dev/packages/core/src/config/normalize.ts.

## Cause 4 (CONFIRMED) — streamed output does not keep clients alive

Output chunks travel as `notifications/message` frames — the
least-load-bearing notification the JSON-RPC 2.0 spec offers — and a
progress notification only reaches a client that sent a `progressToken`
round trip. A client that sends no `progressToken` gains nothing from
streamed stdout. Ralph extracts and propagates progress tokens; the
current behavior is recorded in "Cause 4 — progress-notification path
(CURRENT)" further on.

- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:154-161` builds
  every *output-chunk* notification as

  ```python
  notification = {"jsonrpc": "2.0", "method": "notifications/message", "params": event}
  ```

  and serialises it as `event: message` over SSE.

That means a long exec call whose subprocess emits stdout every few
seconds does NOT extend the client-side MCP timer on any of the
confirmed clients. Claude Code is the documented case, and it has
TWO documented clocks rather than one: the per-server `timeout` is
"a hard wall-clock limit per tool call, and progress notifications
from the server don't extend it", while `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`
"abort[s] with an error instead of waiting for the overall
MCP_TOOL_TIMEOUT" when a server "sends no response and no progress
notification" for that long, "Overrides the per-transport defaults of
300000 (5 minutes) for network servers and 1800000 (30 minutes) for
stdio servers" (https://code.claude.com/docs/en/mcp,
https://code.claude.com/docs/en/env-vars). A streamed
`notifications/message` frame is neither a response nor a progress
notification, so both clocks run down — the wall-clock-plus-idle pair
is the timing that hurts. Codex's `tool_timeout_sec` is likewise a
bare wall clock ("Override the default 60s per-tool timeout for an MCP
server", https://developers.openai.com/codex/config-file/config-reference)
and Kimi's `[mcp] tool_timeout_ms` is a bare 60000 ms wall clock
(https://moonshotai.github.io/kimi-code/en/configuration/config-files);
neither page carries output-conditioned wording.

**Unverified.** No cited page says whether
Kimi's or OpenCode's deadline is *extended* by streamed output. For those
two clients the verified fact is only that neither cited page
documents an output-conditioned extension; treat the mechanism as an
open question, not as an established fact.

Progress notifications reach only clients that honour them, and a client
only counts them when the request carried a `progressToken` round trip.
When a client never sends one, the run report still ends up "client
abandoned, server running" — the state Cause 5 makes observable.

### Cause 4 — progress-notification path (CURRENT)

Under the job and liveness architecture, Ralph extracts the request's
progress token and emits `notifications/progress` frames bound to that
token.

- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:46-69` —
  `extract_progress_token` reads the token from four request shapes:
  `params._meta.progressToken`, `params.progressToken`,
  `params.arguments._meta.progressToken`, and
  `params.arguments.progressToken`. A `str` or `int` token is returned;
  anything else yields `None` (no subscription).
- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:86-104` —
  `_build_progress_frame` renders the SSE frame and its payload:

  ```python
  notification = {
      "jsonrpc": "2.0",
      "method": "notifications/progress",
      "params": params,  # progressToken, progress, optional total
  }
  ```

- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:107-132` —
  `_install_progress_subscription` registers a request-scoped
  `ProgressSubscription` on the shared `ExecJobRegistry` via
  `registry.subscribe_progress`. Bindings are explicit: `exec_wait` /
  `exec_cancel` requests carry `job_id` and bind at install time
  (`:116-131`), while `exec` / `unsafe_exec` / `raw_exec` have no
  `job_id` until the worker calls `registry.start(...)`, so the
  subscription is bound afterwards by
  `ralph/mcp/tools/_exec_jobs.attach_progress_subscription`
  (`_exec_jobs.py:772-787`), which the exec handler hands the subscription
  to at `ralph/mcp/tools/exec.py:645-652`.
- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:167-175` wires
  both mechanisms into the streaming POST: the progress subscription at
  `:167-169` and the output-chunk sink swap at `:173-175`, published to the
  request thread through the `_CURRENT_PROGRESS_SUB` ContextVar
  (`:29-31`) so the exec handler can read it. Teardown at `:193-202`
  unsubscribes, so a torn-down sink never emits to a stale token.

A `grep` over `ralph-workflow/ralph/mcp/server/` for either
`progressToken` or `notifications/progress` returns matches in
`exec_sse_streaming.py` (lines 50, 54, 55, 61, 62, 65, 66, 67, 78, 79,
82, 101, 110, 118, 126, 194, 197). Output chunks still travel as
`notifications/message` (`exec_sse_streaming.py:154-161`), so a client
that sends no `progressToken` gains nothing from streamed stdout.

## Cause 5 (CONFIRMED, restated) — request expiry does not imply process death

Two distinct expiry channels, both of which lose observability:

### 5a. Client expiry

The client gives up on the JSON-RPC round trip at its own tool timeout
(typically the `McpError: MCP error -32001: Request timed out` signal
the operator sees in chat — the verbatim string quoted in issue #8701's
body), but the server-side thread that is running the command keeps
the subprocess alive. There is no client-side abort that reaches
Ralph's process table — the JSON-RPC error happens in the client's
adapter, the server thread sees no signal because no SSE frame has
been closed.

This is the cause of the "X% of long-running exec calls appear to
finish but the subprocess continues to run, holding workspace files
and CPU" complaint: the agent has no visibility into the orphaned
subprocess, and the next iteration of the
watchdog sees an EXPLAIN-attached, half-finished process it cannot
tie back to a specific request. Under the job and liveness architecture,
long jobs are registered in `ExecJobRegistry`, tracked via active liveness
monitoring, and reaped explicitly on termination or session shutdown.

### 5b. Dispatch expiry

`_SaturatedDispatch` returns a `SaturatedResponse` after
`MCP_DISPATCH_TIMEOUT_SECONDS` and calls `future.cancel()`. A *running*
thread cannot be reclaimed: only queued work can be cancelled. The
dispatcher therefore returns the saturation error to its caller and
keeps running the actual command, unobserved:

- `ralph-workflow/ralph/mcp/server/_saturated_dispatch.py:105-115`:

  ```python
  try:
      if self._dispatch_timeout_seconds is None:
          future.cancel()
          return SaturatedResponse(code=SATURATION_CODE, message=SATURATION_MESSAGE)
      return future.result(timeout=self._dispatch_timeout_seconds)
  except concurrent.futures.TimeoutError:
      future.cancel()
      _log.warning("MCP dispatch timed out after %s seconds", self._dispatch_timeout_seconds)
      return SaturatedResponse(code=SATURATION_CODE, message=SATURATION_MESSAGE)
  except concurrent.futures.CancelledError:
      return SaturatedResponse(code=SATURATION_CODE, message=SATURATION_MESSAGE)
  ```

The `future.cancel()` call at `:111` is the failing contract: cancelling a
Future whose callable is a *running* thread is a documented no-op (Python
stdlib docs: `Future.cancel()` returns False for transitions into RUNNING;
the worker has to finish). Ralph therefore has an unobserved-job window
that the saturation cap is supposed to prevent.

### 5c. Session-shutdown job hook exists CHILD-side only

The seam for "a parent kill must reach the child's in-memory registry"
is the child MCP server's shutdown path, not `_session_wrapup.py`.

- `ralph-workflow/ralph/mcp/server/lifecycle.py:1858` and `:1875` invoke
  `_spawn_mcp_process(...)`, which is defined at
  `ralph/mcp/server/lifecycle.py:2077-2091`
  and runs

  ```python
  [
      sys.executable, "-m", "ralph.mcp.server",
      "--host", "127.0.0.1",
      "--port", str(port),
      "--workspace", str(root),
  ]
  ```

  (`ralph/mcp/server/lifecycle.py:2152-2163`; the `"ralph.mcp.server"` module name is at
  `:2156`). The MCP server runs as a separate child
  process; the parent owns the lifecycle via
  `StandaloneMcpProcess.shutdown` (`_standalone_mcp_process.py:36-40`),
  which calls `self.process.terminate(grace_period_s=SHUTDOWN_TERMINATE_GRACE_S)`
  at line 38 and `self.process.wait(timeout=SHUTDOWN_REAP_WAIT_S)` at line 40.
  Both waits are module constants (`_standalone_mcp_process.py:14` and `:16`,
  5.0 seconds each) so the E6.4 restart-budget arithmetic in
  `lifecycle.py:_FAILED_ATTEMPT_CLEANUP_S` is derived from the real teardown
  bound rather than a duplicated literal.

- `ralph-workflow/ralph/mcp/server/_session_wrapup.py` is the
  *development-timebox* warning path: the module docstring reads

  > "Development-timebox wrap-up state shared by MCP tool dispatch.
  > The pipeline publishes `RALPH_DEV_WARN_EPOCH` once for the
  > uninterrupted development phase. MCP reads that epoch for the
  > tool-result warning, so retries cannot restart the timer."

  The module's public surface (`session_before_warning`,
  `session_warning_fired`, `session_warning_scope`,
  `development_wrapup_notice`, `_session_wrapup.py:22-27`) is a
  ContextVar-scoped warning-state publication with no kill-switch
  effect on the in-memory registry.

- The real seam for "session_end kills the child's running jobs"
  is the CHILD process's shutdown path:

  - `ralph-workflow/ralph/mcp/server/runtime.py:625-649` —
    `exit_when_owner_exits` daemon watches the parent PID; when the
    owner is already gone at entry it calls `exit_process(0)`
    (default `os._exit`) at line 638, and the watcher thread does the
    same at line 645 once `get_parent_pid()` stops matching the
    recorded owner.
  - `ralph-workflow/ralph/mcp/server/runtime.py:652-663` —
    `main(argv)` calls `install_shutdown_signal_handlers()` (`:654`),
    then `exit_when_owner_exits(os.environ)` (`:655`), parses args
    (`:656`), and enters `run_standalone_server` (`:657`) which blocks
    on the HTTP server until SIGTERM/Ctrl-C.
  - The `MCP_OWNER_PID_ENV` env var
    (`ralph/mcp/protocol/env.py:45`, `McpEnvVar.OWNER_PID` at `:26`)
    is the parent→child hand-off that lets the watcher know when
    to kill itself.
  - The owner-loss hook the registry installs is registered in
    `ralph/mcp/tools/_exec_jobs.py:275-276`
    (`shutdown_all("session_end")`), run by
    `_run_owner_loss_hooks` (`runtime.py:600-609`) on owner loss and by
    `_run_shutdown_hooks` (`runtime.py:594-597`) on SIGTERM
    (`runtime.py:614-616`) and at exit (`runtime.py:622`).

The parent cannot reach into the child's in-memory collections from
`_session_wrapup.py` (or from any `factory_impl.py` callback the parent
owns) because the subprocess state lives in the child's address space.
The child runtime shutdown path in `ralph/mcp/server/runtime.py`
(`exit_when_owner_exits` and signal handling in `main`/`run_standalone_server`)
is the authoritative execution seam: when the owner process terminates or
`session_end` occurs, the child server reaps its running jobs and
terminates cleanly.

## Cross-reference: how each cause maps to a fix

Each cause has a counterpart that points at it. The chart
records only the seam; the implementation lives in the file named in
the third column.

| Cause | Seam reached | Implementation file |
| --- | --- | --- |
| 1 — `inactivity` is output-only | Liveness sampling and fuzzy assessor with CPU/IO/novelty ladder | `ralph/process/manager/_liveness_assessor.py` |
| 2 — 300 s absolute cap | `EXEC_SOFT_REPLY_BUDGET_MS`, `EXEC_JOB_CEILING_S`, `EXEC_JOB_CEILING_MAX_S` | `ralph/timeout_defaults.py` |
| 3 — client timeout configuration | write documented timeout keys derived from `EXEC_SOFT_REPLY_BUDGET_MS` + headroom | `ralph/mcp/transport/{claude,codex,kimi,opencode,pi}.py` |
| 4 — streamed output does not keep clients alive | SSE notification stream and progress token handling | `ralph/mcp/server/exec_sse_streaming.py` |
| 5 — request expiry does not imply process death | child runtime shutdown path via `exit_when_owner_exits` and standalone server termination | `ralph/mcp/server/runtime.py` |

## Per-path termination matrix

Each row names one way an `ExecJobRegistry`-managed job can be
terminated and records the calling mechanism, the working-tree
citations that prove the mechanism is wired in, and the bound the
producer can guarantee. The matrix is read together with the
kill-cause literals recorded under `Cause 1 — production liveness
subsystem`: the five `KillCause` literals
(`abandoned`, `hard_cap`, `stalled`, `no_progress`, `session_end`)
are how the rows announce themselves to the caller; this matrix
records the seams that produce them.

| path | mechanism | citation (path:line) | proof-or-bound |
| --- | --- | --- | --- |
| SIGTERM of server | Signal handler fires `_run_shutdown_hooks`; registry hook installed at startup calls `registry.shutdown_all("session_end")`; `_kill_job` calls `ManagedProcess.terminate(grace_period_s=0.0)` which escalates to `os.killpg(pgid, SIGKILL)` (preceded by SIGTERM and the git cleanup window when the group contains git) over the worker's process group, taking worker + descendant in one group-kill. | `ralph/mcp/server/runtime.py:612-619` (handler install) → `ralph/mcp/server/runtime.py:594-597` (`_run_shutdown_hooks`) → `ralph/mcp/tools/_exec_jobs.py:275-276` (registry hook) → `ralph/mcp/tools/_exec_jobs.py:722` (`shutdown_all`) → `ralph/mcp/tools/_exec_jobs.py:406-416` (`_kill_job`) → `ralph/process/manager/_managed_process.py:550` (`terminate`) → `ralph/process/manager/_process_manager.py:978` (`cleanup_orphans`) / `:1004` (`_kill_pgid`) | **Proof**: `tests/test_exec_job_shutdown_subprocess_e2e.py:92` (`test_sigterm_shutdown_kills_worker_and_descendant`) — worker + descendant reaped, `kill_cause == "session_end"`. Bound: the entire shutdown completes within `SHUTDOWN_TERMINATE_GRACE_S + SHUTDOWN_REAP_WAIT_S = 10s`. |
| Owner SIGKILL with server surviving | `exit_when_owner_exits` daemon watches `os.getppid()`; when it stops matching the recorded owner, it invokes `_run_owner_loss_hooks` (a daemon thread plus a bounded join at `EXEC_OWNER_LOSS_HOOK_BUDGET_S`), the registry hook fires `registry.shutdown_all("session_end")`, and the same group-kill path as SIGTERM completes the tree. The same sequence fires immediately at entry when the stored owner PID never matched the current PPID (already-orphaned case). | `ralph/mcp/server/runtime.py:625-649` (`exit_when_owner_exits`) → `ralph/mcp/server/runtime.py:600-609` (`_run_owner_loss_hooks`) → immediate fire at `ralph/mcp/server/runtime.py:637`, watcher fire at `ralph/mcp/server/runtime.py:644` → registry hook at `ralph/mcp/tools/_exec_jobs.py:275-276` → group-kill same as the row above. | **Proof**: `tests/test_exec_job_shutdown_subprocess_e2e.py:130` (`test_owner_loss_watcher_kills_worker_and_descendant`) — owner SIGKILLed, child server's owner watcher fires the pre-exit hook, worker + descendant reaped. Bound: the watch loop polls at the configured sleep interval (`sleep(1.0)` at `ralph/mcp/server/runtime.py:643`, bound to `time.sleep` as the production default at `ralph/mcp/server/runtime.py:629`); the hook thread is bounded by `EXEC_OWNER_LOSS_HOOK_BUDGET_S`. |
| MCP server SIGKILLed (Linux) | No Python hook runs on SIGKILL — atexit and `atexit.register(_run_shutdown_hooks)` (`runtime.py:622`) are skipped and no signal handler can fire. Tree death depends on the kernel: on Linux, off-main-thread exec-job spawns (the production HTTP dispatch path) route through the supervisor process `ralph/process/manager/_supervisor.py` (`start_new_session=True`), which arms `PR_SET_PDEATHSIG=SIGTERM` against the server holder and runs an `os.killpg(pgid, SIGKILL)` sweep over the process group on holder death, while the forked worker child arms `PR_SET_PDEATHSIG=SIGKILL` against the supervisor; main-thread spawns install `_install_pdeathsig` as the ProcessManager preexec (`prctl(PR_SET_PDEATHSIG, SIGKILL)`). | `ralph/process/manager/_supervisor.py:72-84` (the `killpg` PG sweep in `_kill_tree_and_exit`) and `ralph/process/manager/_supervisor.py:106-138` (`run`, the supervisor lifecycle) — `ralph/process/manager/__init__.py:148-163` (`_supervisor_invocation`) — `ralph/process/manager/__init__.py:165-212` (`_default_sync_process_factory`) — `ralph/process/manager/__init__.py:127-147` (`_install_pdeathsig`) and `ralph/process/manager/__init__.py:214-260` (`_default_async_process_factory`). | **Proof**: `tests/test_exec_job_shutdown_subprocess_e2e.py:269` (`test_worker_thread_sigkill_kills_exec_job_tree_and_its_descendant`, marked `subprocess_e2e`, Linux-only) — starts registry via worker thread, forks descendant, SIGKILLs holder, and asserts both worker and descendant die; additionally `tests/test_exec_job_shutdown_subprocess_e2e.py:221` (`test_parent_sigkill_kills_exec_job_via_pr_set_pdeathsig`) for direct main-thread preexec. Bound: sub-millisecond kernel delivery plus supervisor `killpg` sweep. |
| MCP server SIGKILLed (non-Linux) | SIGKILL cannot run any Python hook on any platform — no atexit, no signal handler, no watchdog. On Linux the supervisor and preexec close the gap (see Linux row above); on macOS/Windows no `PR_SET_PDEATHSIG` exists, the gating `sys.platform.startswith("linux")` test short-circuits supervisor and preexec routing, and the PDEATHSIG test skips. **Nothing in-process bounds the lifetime of a job orphaned by a server SIGKILL on these platforms**: once the server is dead, its `ExecJobRegistry`, liveness sampler threads, the abandonment timer (`EXEC_JOB_ABANDON_S` lives in the now-dead process), and all signal/atexit hooks are gone with it. The orphan's worker and descendants are re-parented to PID 1 and run to natural completion; OS-level process cleanup alone does not terminate them. | `ralph/process/manager/__init__.py:165-212` (sync supervisor/preexec gate) and `ralph/process/manager/__init__.py:214-260` (async preexec gate — the `_death_preexec(command) if sys.platform.startswith("linux") and threading.current_thread() is threading.main_thread() else None` ternary inside `_default_async_process_factory`) — **pre-SIGKILL caps that DO NOT bind a SIGKILLed-server orphan**: `ralph/mcp/tools/_exec_jobs.py:266-267` (`_running_jobs` capped at `EXEC_MAX_RUNNING_JOBS=4` prevents new submissions from leaking past the cap while the server is alive; `_finished_results` is a `deque(maxlen=EXEC_JOB_RESULT_FIFO=16)`); constants at `ralph/timeout_defaults.py:177` and `:179`, and the abandonment window `EXEC_JOB_ABANDON_S = 120.0` at `ralph/timeout_defaults.py:175` (does NOT fire on an orphan whose server is dead). | **Stated, not proven.** No non-Linux test exists; `tests/test_exec_job_shutdown_subprocess_e2e.py:221-268` skips with the reason "PR_SET_PDEATHSIG is Linux-only and no cross-platform parent-death equivalent is available; once the MCP server is SIGKILLed on a non-Linux platform there is no in-process lifetime bound for an orphaned exec-job tree." **Today's actual bound on the orphan: NONE in-process after the server is SIGKILLed.** The pre-SIGKILL caps cited above prevent new submissions from leaking past the cap while the server is alive; they do NOT cause an in-flight SIGKILLed-server orphan to exit. `EXEC_JOB_ABANDON_S` lives in the (now-dead) server process. The orphan's lifetime is therefore bound by: (a) the orphan runs to natural completion, (b) OS-level cleanup when it exits, or (c) an external supervisor outside this codebase. **Pre-SIGKILL caps retained only for completeness**, not as a bound on the orphan itself. |
| Post-restart `exec_wait` on a lost job | The in-memory registry is destroyed when the server restarts, so any `exec_wait(old_job_id)` falls through the lookup and raises a non-retryable "unknown or expired job; process was killed or lost in server restart" error. It never fabricates a snapshot or a result. | `ralph/mcp/tools/_exec_jobs.py:631-654` (`_lookup_job_locked`; the literal message string at `ralph/mcp/tools/_exec_jobs.py:653`) | **Proof**: `tests/test_exec_jobs_session_lifecycle.py:47` (`test_restart_lost_fresh_registry_returns_nonretryable_lost_in_server_restart_error`) — the `:63-68` branch asserts the same string for an invented, never-seen `job_id`, proving the new registry cannot distinguish prior-server ids from any other id (the correct behaviour). Bound: immediate. |
| Normal job completion | The registry runner polls the handle each `EXEC_LIVENESS_SAMPLE_INTERVAL_S` (2 s); a non-`None` returncode marks the job finished with `kill_cause=None`, then `_settle_job` calls `pm.cleanup_orphans(handle)` to kill descendants that outlived the root and gives the reader threads a bounded window (`_READER_DRAIN_TIMEOUT_S`, 1 s) to reach EOF. The job slides from the running map to the bounded results FIFO before `done_event` wakes waiters. The finisher never closes a pipe: each reader closes its own stream at EOF, so a detached (`setsid`) descendant that escaped the group kill and still holds the pipes cannot stop the job from finishing; the result carries the output captured so far. | `ralph/mcp/tools/_exec_jobs.py:534-541` (poll-and-mark) — `ralph/mcp/tools/_exec_jobs.py:418-425` (`_mark_finished`) — `ralph/mcp/tools/_exec_jobs.py:356-359` (`_settle_job` → `pm.cleanup_orphans`) — `ralph/process/manager/_process_manager.py:978` (`cleanup_orphans`) / `:1004` (`_kill_pgid`) | **Proof**: orphan sweep locked by `tests/test_exec_jobs.py:463` (`test_trace_1_root_exit_with_orphaned_child`) and `tests/test_exec_jobs.py:159` (`_TrackingProcessManager.cleanup_orphans`). Bound: `EXEC_LIVENESS_SAMPLE_INTERVAL_S = 2.0` (`ralph/timeout_defaults.py:137`) is the worst-case staleness between root exit and the registry noticing. |
| Session end | `session_end` is the `KillCause` literal stamped onto the `JobResult` whenever `shutdown_all()` fires; the trigger is whichever path delivered the teardown — SIGTERM via `install_shutdown_signal_handlers`, owner SIGKILL via `exit_when_owner_exits`, or the lifecycle's `shutdown_mcp_server(bridge)` teardown, which calls `bridge.shutdown()` and triggers the child server's own SIGTERM-handler chain. There is no separate "session end" Python mechanism; it is the union of those paths. | kill-cause literal at `ralph/mcp/tools/_exec_jobs.py:722` (also rendered as `KillCause` member of the literal block at `ralph/mcp/tools/_exec_jobs.py:52-54`); lifecycle teardown at `ralph/mcp/server/lifecycle.py:2300-2301` (`shutdown_mcp_server`) → `ralph/mcp/server/_standalone_mcp_process.py:36-40` (`StandaloneMcpProcess.shutdown`); constants at `ralph/mcp/server/_standalone_mcp_process.py:14` and `:16` (5.0 s each) | **Proof**: `tests/test_exec_job_shutdown_subprocess_e2e.py:92-129` reads `CAUSE session_end` off the registry's `wait()` output, proving the literal renders through the SIGTERM path. Bound: teardown wall-clock ≤ `SHUTDOWN_TERMINATE_GRACE_S + SHUTDOWN_REAP_WAIT_S = 10s`. |

The matrix is the single point of truth for the seaming. A code edit
that changes any of the cited lines without updating this table is a
drift edit; the per-row proof-or-bound column is what the
"kills the tree" claims have to back up against.

## Deviations (PENDING-RECONCILIATION)

PA-001 escalation: criteria 3 and 6 are PENDING-RECONCILIATION with external product authority.
The scenario-8 / scenario-13 effective kill times are
unreconciled against the spec's "killed at `stall_window` after activity stops"
wording, and the spec-internal score / kill-time monotonicity stands as an open
conflict. Both findings are proven mathematical limits of the spec's D2/D3
architecture awaiting authoritative product reconciliation, recorded on
`path:line` evidence, not resolved or accepted by developer authority.

### A. Ramp-offset timing — mathematical limit of spec D2/D3 wording (PENDING-RECONCILIATION)

The spec table reads "killed at `stall_window` after activity stops".
The closed-form derivation reveals that the elapsed time from **pressure onset**
($o = 35$ s, the instant past last output where $s < \theta$) to the kill is
`stall_window + ramp_accrued = 90 + 12.5 = 102.5 s` (not `stall_window`),
because during the 25 s ramp $[35, 60]$ only $12.5$ s of stall pressure accrues
($P = 12.5$), leaving $77.5$ s of flat-line accrual past $o = 60$. The total
elapsed time past last output is thus `ramp_end + (stall_window - ramp_accrued) = 60 + 77.5 = 137.5 s`
(continuous) / `138 s` (coarse). The spec-table wording "at stall_window after
activity stops" is an approximation; the closed-form formula
$t_{\text{kill}} = \text{last\_output} + 60 + (\text{stall\_window} - 12.5)$
is the exact behavior. Pressure accrues only
while `s < θ` (`EXEC_STALL_THETA` = 0.5); the score in the dead phase
collapses to `s = OutputRecent(o)`, and `OutputRecent(o) >= 0.5` for the
first 35 s past the last output (half-way through the linear ramp
`OutputRecent(o) = (60 - o) / 50` for `o` in `(10, 60]`). Pressure
therefore does not begin to accrue until `o = 35 s`, and the trapezoidal
ramp `[35, 60]` accrues 12.5 s of stall pressure before the score
saturates at 0:

```
ramp_accrued_pressure = integral over [35 -> 60] of (o - 35) / 25  =  0.5 * 25  =  12.5 s
```

so the empirical all-idle kill time is

```
t_kill = last_output + ramp_end + (stall_window - ramp_accrued)
       = last_output + 60    + (stall_window - 12.5)
       = last_output + (stall_window + 47.5)
```

evaluated on the coarse 2 s sample grid (`EXEC_LIVENESS_SAMPLE_INTERVAL_S`
= 2.0). For the shipped defaults (`stall_window = 90` s) that is
`last_output + 137.5 s` continuous, to within one sample interval.
Worked numbers (scenario-library ground truth):

- scenario 8 ("short burst then idle",
  `ralph-workflow/tests/test_exec_liveness_scenarios.py:244`) —
  `burst_stop = 30`, kill at **168 s** (continuous 167.5 s)
- scenario 13 ("mid-run deadlock",
  `ralph-workflow/tests/test_exec_liveness_scenarios.py:424`) —
  `deadlock_at = 120`, kill at **258 s** (continuous 257.5 s)

`ralph/mcp/ARCHITECTURE.md` section 3 ("Ramp-offset timing") and
section 4 ("Stall Integrator") carry the formula; this page carries the
worked numbers and the deviation framing. Acceptance criterion 3
("idle command killed with cause `stalled` within `stall_window` plus
one sample interval") holds for a trace idle from the start
(`last_output = 0`); the scenario library asserts the tighter bound
`last_output + ramp_end + (stall_window - ramp_accrued)` within one
sample interval for both the burst-then-idle and the mid-run-deadlock
shapes. The deviation for traces with prior activity is PENDING-RECONCILIATION
with external product authority to reconcile the spec table wording
against the mathematical consequences of the specified membership curves.

### B. Strict-Monotonic score / kill-time — mathematical limit under literal-Sugeno aggregate (PENDING-RECONCILIATION)

The shipped aggregate is the literal-Sugeno weighted average
`sum(w_i * z_i) / sum(w_i)` verbatim from D2/D3; that aggregate admits
dilution, and the spec's strict-Monotonic property is unsatisfiable
jointly with the prescribed aggregate. The counterexample is locked at
`ralph-workflow/tests/test_liveness_pa002_properties.py:223`
(`test_kill_timing_non_monotone_under_changing_io`): an idle / working /
idle trace kills at **130 s** with `io = 0` in the working segment and
strictly earlier (**129 s**) when `io` rises to 64 KiB/s. R2's `z = 0.9`
dilutes R1's `z = 1.0`, the score falls from 1.0 to 0.95, and the
per-step delta becomes less negative so pressure drains less over the
working segment.

The deviation is **PENDING-RECONCILIATION** with external product authority:
it reflects a proven mathematical impossibility of satisfying both the
literal-Sugeno aggregate prescribed in D2/D3 and strict score/kill-time
monotonicity simultaneously. The literal-Sugeno aggregate is retained
as implemented (D2/D3 verbatim, without arbitrary developer-side scoring-code
changes). Product authority must determine whether to relax the strict
monotonicity criterion or revise the Part A aggregate. The rest of the spec's
property list holds under the shipped aggregate:

- **Baseline-relative score monotonicity** — from an all-idle window
  (R5 only, `s = 0`), raising any single working signal non-decreases
  `s`.
- **Baseline-relative classification safety** — raising working signals
  never demotes a working/suspicious verdict to stalled/unknown.
- **Determinism and bounds** — the same window and config produce the
  same verdicts; `P` is never negative; an all-idle trace is killed at
  `stall_window` plus or minus one integration interval.
- **Fail-closed unknowns** (D5) — replacing a signal with unknown never
  delays a kill compared with the same trace at that signal's zero.

## Disposition (PENDING-RECONCILIATION)

The monotonicity and ramp-offset timing dispositions are
**PENDING-RECONCILIATION** with external product authority, as detailed in
the "Deviations (PENDING-RECONCILIATION)" section above. The mathematical
proofs demonstrate that these deviations cannot be eliminated within the
confining requirements of the current D2/D3 spec without product-level
trade-offs (such as weakening C2 to meet C3, or changing the Part A aggregate
to satisfy strict monotonicity). The evidence that backs this analysis —
`tests/test_liveness_assessor.py`,
`tests/test_liveness_pa002_properties.py`,
`tests/test_liveness_properties.py`, and the
`Cause 1 — production liveness subsystem (CURRENT)` rule table — is on
file for the product reconciliation process.

## Appendix: how to verify the doc is still truthful

The five causes are stable as long as the file:line citations in this
doc match the working tree. Drift is the only way a citation goes
stale, and the fastest drift check is a re-read of:

- `ralph-workflow/ralph/timeout_defaults.py:37` (`EXEC_DEFAULT_TIMEOUT_MS`)
  and `:39` (`EXEC_MAX_TIMEOUT_MS`), plus `:169`
  (`EXEC_SOFT_REPLY_BUDGET_MS`), `:171` (`EXEC_JOB_CEILING_S`), `:173`
  (`EXEC_JOB_CEILING_MAX_S`) — the Cause 1 / Cause 2 constants.
- `ralph-workflow/ralph/process/manager/_managed_process.py:279` (the
  output-only `last_output_at_ref` reset) and `:344-360` (the poll
  loop that computes `inactivity_deadline` and arms the kill)
- ralph-workflow/ralph/mcp/tools/exec.py:558-571 (the injected-runner
  seam) and `:572-607` (the production registry entry path) — reading
  these two ranges is how a reader tells whether the output-only window
  is still reachable for normal calls.
- `ralph-workflow/ralph/mcp/tools/_exec_jobs.py:194-198` (the registry
  `hard_cap` ceiling resolution), `:449` (the kill check against
  `job.ceiling_ms`) and `:52-54` (the six kill causes)
- `ralph-workflow/ralph/mcp/transport/opencode.py:26-28` (the
  `_OPENCODE_MCP_CLIENT_TIMEOUT_MS` constant both timeout keys are
  written from), `:77` (the per-server `timeout` write), `:91` (the
  `experimental.mcp_timeout` write) and `:116` (the `$schema` value the
  OpenCode schema-drift claim in the verified table is read against)
- `ralph-workflow/ralph/mcp/transport/claude.py:49`,
  `ralph-workflow/ralph/mcp/transport/codex.py:127`,
  `ralph-workflow/ralph/mcp/transport/kimi.py:34`,
  `ralph-workflow/ralph/mcp/transport/pi.py:59`
  (the rest of Cause 3's per-transport writes)
- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:46-69`
  (`extract_progress_token`), `:86-104` (`_build_progress_frame`
  emitting `notifications/progress`) and `:154-161` (the
  `notifications/message` output path) — Cause 4
- `ralph-workflow/ralph/mcp/server/_saturated_dispatch.py:105-115`
  (Cause 5b)
- `ralph-workflow/ralph/mcp/server/runtime.py:625-649`
  (`exit_when_owner_exits`) and `:652-663` (`main`) — the Cause 5c seam
- `ralph-workflow/ralph/mcp/server/lifecycle.py:1858` and `:1875` (the
  two `_spawn_mcp_process` call sites), `:2077-2091` (its definition) and
  `:2152-2163` (the `sys.executable -m ralph.mcp.server` argv list)
- `ralph-workflow/ralph/mcp/server/_standalone_mcp_process.py:14` and
  `:16` (`SHUTDOWN_TERMINATE_GRACE_S` / `SHUTDOWN_REAP_WAIT_S`, 5.0 s each)
- `ralph-workflow/ralph/mcp/server/_timing_safety.py:35`
  (`TIMING_SAFETY_TRANSPORT_HEADROOM_MS`) and `:37-39`
  (`TIMING_SAFETY_TRANSPORT_FLOOR_MS` — the per-transport floor the
  written keys must clear)

The URL citations in the verified client-timeout table are also
subject to drift — every URL listed there was fetched live and the
quoted string was read back off the live page before the row was
allowed to stand. Re-running
`scripts/fabrication_guard.py --level 2
ralph-workflow/docs/agents/exec-long-running-root-cause.md`
after this doc is edited is the cheapest way to confirm none of
them have since 404'd — but note what level 2 does and does not
prove. It resolves the GitHub repos through the API and HEAD-checks
markdown-link URLs; it never opens prose, so a vendor page that
silently renames or re-defines the field it documents still passes
level 2 with exit 0. Reading the quoted text back off the live page is
the only check that catches that class of drift. A row that reads
`UNKNOWN` is not a defect; it is the doc being honest about what the
cited docs page does and does not name, and an `Unverified` clause
inside a row is the same honesty at smaller grain.

If any of the file:line citations above move, this doc must update
before the fix it references is merged. Fix agents use
this doc as a target; an out-of-date citation is the same as a
missing citation.
