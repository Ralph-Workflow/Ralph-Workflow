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
  client. "Verified" requires an explicit line from a URL an agent
  fetched live: the WU-5 verification pass, with every URL re-opened
  and the quoted string read back off the page in the WU-3 citation
  pass. `UNKNOWN (no docs surface)`
  means the cited docs page does not name a tool-call timeout at all and
  we did not find one in the transport source — Ralph cannot size what
  the docs do not expose. `UNKNOWN (docs say "SDK default 60 s")` means
  the docs explicitly punt to the MCP SDK's hard-coded default and the
  value is therefore documentation-dependent, not Ralph-writable.
- **Source/citation** — the live URL(s) the row's claims were taken
  from. Every URL in this column was fetched during the WU-5 pass and
  re-fetched in the WU-3 citation pass; all of them still resolve and
  all of them still carry the quoted text. Per-client transport
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
| Codex CLI | per-server `tool_timeout_sec` (integer seconds) under `mcp_servers.<name>` — written at `ralph/mcp/transport/codex.py:227` | **Verified**: per-server `tool_timeout_sec` "Override the default 60s per-tool timeout for an MCP server" — cited verbatim from the Codex Configuration Reference page | https://developers.openai.com/codex/config-file/config-reference (verified, resolves to https://learn.chatgpt.com/docs/config-file/config-reference; `mcp_servers.<id>.tool_timeout_sec` row) |
| Claude Code | per-server `timeout` (number, ms) in the server's `.mcp.json` entry — written into the synthesized `--mcp-config` payload at `ralph/mcp/transport/claude.py:80`; env fallback `MCP_TOOL_TIMEOUT`; for an HTTP, SSE, or claude.ai-connector server a SECOND, independent per-request timer set to the greatest of 60 s, that server's per-server `timeout`, and `MCP_TIMEOUT` | **Verified**: per-server `timeout` is "a hard wall-clock limit per tool call"; "Values below 1000 are ignored and fall through to MCP_TOOL_TIMEOUT, or to its default of about 28 hours when that variable is unset"; the second per-request timer is "the greatest of three values: 60 seconds, the tool timeout that applies to the server, and MCP_TIMEOUT". A THIRD documented clock, `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`, "abort[s] with an error instead of waiting for the overall MCP_TOOL_TIMEOUT" when a server "sends no response and no progress notification" for that long, and "Overrides the per-transport defaults of 300000 (5 minutes) for network servers and 1800000 (30 minutes) for stdio servers". The 75 000 ms Ralph writes clears every documented floor: above 60000 so it raises the 60-second per-request limit, and at least 1000 so "a per-server timeout of at least 1000 also sets the minimum idle window for that server's tool calls" | https://code.claude.com/docs/en/mcp (verified) AND https://code.claude.com/docs/en/env-vars (verified — `MCP_TOOL_TIMEOUT` row reads "Timeout in milliseconds for MCP tool execution (default: 100000000, about 28 hours)"; the `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` row supplies the idle defaults) |
| OpenCode | env or global `experimental.mcp_timeout` AND documented-but-ignored per-server `mcp.<server>.timeout` — Ralph writes BOTH at `ralph/mcp/transport/opencode.py:131` and `:148`; the experimental key is the one that wins (issue #8701) | **Verified**: per-server `timeout` is documented as "Timeout in ms for fetching tools from the MCP server. Defaults to 5000 (5 seconds)" — explicitly a TOOL-FETCH deadline, not the tool-call deadline. The actual tool-call enforcement is the MCP SDK's hard-coded default (~60 s). Issue #8701 (closed) confirms: per-server `timeout` is ignored for tool calls; `experimental.mcp_timeout` is the field the SDK honours. **Schema-drift item RECONCILED — CLOSED, no Ralph-side write required (re-checked in this pass against the OpenCode config normalizer, not just the published schema):** the machine-readable schema Ralph stamps into its own generated config (`$schema` written at `ralph/mcp/transport/opencode.py:175`) marks `experimental.mcp_timeout` `"deprecated": true` and adds a non-deprecated `timeout` OBJECT with `startup` / `catalog` / `execution` sub-keys, at both `mcp.timeout.*` (global) and per-server `mcp.<server>.timeout.*`. The human-facing MCP docs page has NOT been updated and still documents only the scalar per-server `timeout`, so the DOCS remain behind the SCHEMA — that half of the earlier note stands unchanged. What does NOT follow is the earlier conclusion that Ralph must therefore also write `timeout.execution`: that gap is closed by OpenCode's own normalizer, on four independently cited grounds. **(1)** `execution` is the real tool-call wall, not a documentation artifact — it is the sub-key the MCP client resolves and hands to the tool-call deadline, and OpenCode's own default for it is `12 * 60 * 60 * 1_000 // 12 hours`, so the current tool-call ceiling is hours, not the ~60 s the 1.x-era issue reports describe. **(2)** OpenCode translates Ralph's deprecated key onto the superseding key itself: the config normalizer reads `experimental.mcp_timeout` and assigns that one value to BOTH `timeout.catalog` and `timeout.execution`, so the field Ralph already writes at `ralph/mcp/transport/opencode.py:148` lands on `timeout.execution` without Ralph emitting it. **(3)** Ralph's per-server scalar already expands into the object form: every legacy `mcp.<server>` entry is routed through `migrateMcp`, whose `migrateMcpTimeout` returns `{ startup: timeout, catalog: timeout, execution: timeout }`, so Ralph's 330000 ms at `ralph/mcp/transport/opencode.py:131` sets `execution` to 330000 ms as well. **(4)** On the build this row pins and Ralph tests against, the key does not exist at all: OpenCode v1.18.25 (named at `ralph/mcp/transport/opencode.py:184`) models the `Timeout` object as `{ startup, request }`, with `request` described as "Maximum time in milliseconds to wait for each MCP request after initialization", so `timeout.execution` on that build is a sub-key no schema there defines. Emitting the GLOBAL `mcp.timeout.execution` would also be actively harmful rather than merely redundant: the global object is a FILL-IN default merged per server as `{ ...timeout, ...server.timeout }`, so a global `execution` of 330000 would pull every operator-declared MCP server's tool calls down from OpenCode's 12-hour default to ~5.5 minutes — a regression for third-party servers, which is exactly the population this transport must not degrade. Ralph's existing writes are therefore sufficient and this row does NOT depend on a field on a deprecation path | https://opencode.ai/docs/mcp-servers/ (verified) AND https://github.com/anomalyco/opencode/issues/8701 (verified — title "Per-server MCP timeout setting not working; requires experimental.mcp_timeout instead", state=closed) AND https://github.com/anomalyco/opencode/issues/8121 (verified — title "MCP timeout not respected", state=closed) AND https://opencode.ai/config.json (verified — the published config schema both `experimental.mcp_timeout` and the `timeout.execution` object are read from) AND https://github.com/anomalyco/opencode/blob/dev/packages/core/src/config/normalize.ts (verified — `normalizeMcp` copies `experimental.mcp_timeout` into BOTH `timeout.catalog` and `timeout.execution`, and routes legacy `mcp.<server>` entries through `migrateMcp`) AND https://github.com/anomalyco/opencode/blob/dev/packages/core/src/v1/config/migrate.ts (verified — `migrateMcpTimeout` returns `{ startup: timeout, catalog: timeout, execution: timeout }`) AND https://github.com/anomalyco/opencode/blob/dev/packages/core/src/mcp/client.ts (verified — `execution` is the resolved value passed to the `client.callTool(...)` deadline, defaulting to `12 * 60 * 60 * 1_000`) AND https://github.com/anomalyco/opencode/blob/dev/packages/core/src/config/plugin/mcp.ts (verified — the global `mcp.timeout` is a fill-in default merged per server as `{ ...timeout, ...server.timeout }`) AND https://github.com/anomalyco/opencode/blob/v1.18.25/packages/core/src/config/mcp.ts (verified — `Timeout` is `{ startup, request }`; no `execution` sub-key) |
| Cursor | none written — `cursor_mcp_config` emits only `{"url": endpoint}` at `ralph/mcp/transport/cursor.py:87-92`; Cursor's docs page has no tool-call-timeout field to write | **UNKNOWN (no docs surface)**: the Cursor MCP docs page lists STDIO fields `type`/`command`/`args`/`env`/`envFile` and remote `url`/`headers`/`auth` and stops there. No timeout field is named. We could not verify a client default. | https://cursor.com/docs/mcp (verified — no tool-call-timeout field documented) |
| Kimi Code CLI | per-server `toolTimeoutMs` (number, ms) in `mcp.json` — written at `ralph/mcp/transport/kimi.py:119`; plus the global default `[mcp] tool_timeout_ms` in `config.toml` (out of Ralph's write path) and the env override `KIMI_MCP_TOOL_TIMEOUT_MS` | **Verified**: global `[mcp] tool_timeout_ms` default is 60000 ms (60 seconds). Per-server `toolTimeoutMs` overrides the global; `KIMI_MCP_TOOL_TIMEOUT_MS` overrides the file. Cited verbatim from the Kimi `mcp` table | https://moonshotai.github.io/kimi-code/en/configuration/config-files (verified — `mcp` table, "Global default single tool-call timeout in milliseconds for all MCP servers; a per-server `toolTimeoutMs` in `mcp.json` wins") AND https://moonshotai.github.io/kimi-code/en/customization/mcp (verified — `toolTimeoutMs` per-server field) |
| Nanocoder | none written for tool-call — `nanocoder` payload at `ralph/mcp/transport/nanocoder.py:56-61` writes `{"transport": "http", "url": endpoint}` plus optional `alwaysAllow`; the package's documented `timeout` is a connection-timeout, not a tool-call-timeout | **UNKNOWN (docs say "SDK default 60 s")**: Nanocoder docs state the per-server `timeout` "Bounds the connection handshake and the initial tool listing; individual tool calls keep the MCP SDK's default timeout (60 seconds)" — the per-tool default is whatever the MCP SDK ships with, not a value Ralph can write | https://docs.nanocollective.org/nanocoder/docs/v1.31.0/configuration/mcp-configuration/ (verified — `timeout` row in Common Fields: "Connection timeout in milliseconds. Bounds the connection handshake and the initial tool listing; individual tool calls keep the MCP SDK's default timeout (60 seconds)") AND https://registry.npmjs.org/@nanocollective/nanocoder (verified — `dist-tags.latest=1.31.0`; `repository` points at the GitHub repo below) AND https://github.com/Nano-Collective/nanocoder (verified — repo exists; README states "Full documentation is available online at docs.nanocollective.org") |
| Pi | bridge constant `MCP_TOOL_CALL_DEADLINE_MS` — written into the generated TypeScript bridge at `ralph/mcp/transport/pi.py:306`; bridge also implements dual-abort with Pi's per-tool signal (`transport/pi.py:300-318`); discovery is gated by `MCP_STARTUP_TIMEOUT_MS = 10_000` (`transport/pi.py:85`) | **PARTIALLY verified — Pi documents a 60-second per-request timeout, but not on the path Ralph uses.** Pi's MCP docs state that both server types support "`timeout`: per-request timeout in seconds (default 60). Progress notifications reset it." That `timeout` configures **Pi's own MCP client** (`mcpServers` entries in `~/.pi/agent/mcp.json` / `.pi/mcp.json`, or `pi.registerMcpServer`), and Ralph's transport does NOT go through it: Ralph generates a Pi *extension* that registers Pi custom tools and issues a direct `fetch` to Ralph's own HTTP MCP endpoint (`fetch(ENDPOINT, { signal })`, per the generated bridge at `ralph/mcp/transport/pi.py:200`). Pi's 60-second default therefore never arms on Ralph's path. The only Pi-side clock that reaches the bridge is the per-tool `AbortSignal` that `registerTool({ execute(_, _, signal) })` passes down, and **the duration of that signal is not stated on any cited Pi page** — that residue, not "no docs surface at all", is what this row's `UNKNOWN` covers, so the bridge-side deadline remains the only verified bound. Note also that Pi's own two pages disagree on the unit: the `registerMcpServer` reference lists `timeout` (number) as "Timeout in milliseconds" while the MCP page says seconds; this row therefore quotes the MCP page's default and does not assert a unit for the API-reference field | https://pi.dev/docs/latest/mcp (verified — "`timeout`: per-request timeout in seconds (default 60). Progress notifications reset it.") AND https://pi.dev/docs/latest/extensions (verified — `pi.registerMcpServer` config field list, `timeout` (number) "Timeout in milliseconds") AND `ralph-workflow/ralph/mcp/transport/pi.py:42-318` (verified — read in working tree; the bridge's own `fetch` call is at `:200`) |
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
    (`ralph/timeout_defaults.py:598`) caps how long a single exec call
    may run before the server returns; the transports above derive
    their per-client timeout from this budget + 30 s of headroom
    (the `TIMING_SAFETY_TRANSPORT_HEADROOM_MS` constant at
    `ralph/mcp/server/_timing_safety.py:84`); OpenCode is the one
    exception — `ralph/mcp/transport/opencode.py:50-51` sizes it as
    `max(EXEC_MAX_TIMEOUT_MS, EXEC_SOFT_REPLY_BUDGET_MS) + 30 s` =
    330 s.
  - **Server-side hard cap** — `EXEC_MAX_TIMEOUT_MS = 300_000` (5
    minutes, `ralph/timeout_defaults.py:142`) bounds the
    injected-runner seam, and `EXEC_JOB_CEILING_S` /
    `EXEC_JOB_CEILING_MAX_S` (`ralph/timeout_defaults.py:603` and
    `:609`) cap production job wall-clock independently of any client
    default (Cause 2 below).
  - **Server-side child-runtime shutdown** — the
    `exit_when_owner_exits` daemon in `ralph/mcp/server/runtime.py`
    (`:914-950`) reaps running subprocesses when the parent
    process dies, closing the "request expiry does not imply process
    death" gap regardless of what the client default is.
  - **Server-side liveness-based kill** — under the job and liveness
    architecture, the §3b fix layers a fuzzy liveness assessor
    (`ralph/process/manager/_liveness_assessor.py`) over the long-running
    job flow. Historically (prior to this fix), a long CPU-busy exec was
    killed by the output-only inactivity window at `EXEC_DEFAULT_TIMEOUT_MS`.
    Under the current job and liveness architecture, a command that stays
    silent on stdout but is busy on CPU, disk I/O, child-process activity,
    or voluntary context switches no longer dies at the
    `EXEC_DEFAULT_TIMEOUT_MS` inactivity window; it lives as long as its
    liveness score stays above the stall-pressure kill line, up to the
    registry's `hard_cap` ceiling (`EXEC_JOB_CEILING_MAX_S = 4 h`).
    Cause 1 below records the historical output-only inactivity behavior
    that the §3b fix supersedes — see "Cause 1 — present-day liveness
    subsystem" further on for the current behavior.

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
below were read from the working tree in the WU-5 pass and re-read
against the working tree in the WU-3 citation pass; if they drift,
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
  TypeScript bridge; `pi.py:85` defines `MCP_STARTUP_TIMEOUT_MS = 10_000`
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

## Cause 1 (CONFIRMED) — `inactivity` is output-only (HISTORICAL)

> **Status note.** Cause 1 documents the **historical** behavior of the
> exec subprocess timeout — the *pre-§3b* output-only inactivity window
> that the liveness-based subsystem has since superseded. The §3b fix
> replaces this rule for the whole production entry path: normal `exec`
> always becomes a registry job (`ralph/mcp/tools/exec.py:792-821`), so
> the inactivity window is no longer the kill rule for short in-request
> calls either — it survives only on the injected-runner seam
> (`ralph/mcp/tools/exec.py:777-790`). See "Cause 1 — present-day
> liveness subsystem" further on for current behavior. This section is
> preserved so a reader can tell what the old failure mode was, and so
> the WU-5 citations remain anchored to the source code they describe.

Historically (prior to this fix), the timeout on the exec subprocess was
an *output-only* inactivity window; it was refreshed only by a chunk from
the child's stdout/stderr, never by CPU work.

- `ralph-workflow/ralph/process/manager/_managed_process.py:315` resets
  `last_output_at_ref[0] = _time.monotonic()` inside `_read_output_stream`
  only when a non-empty `chunk` arrives from the child's `stdout`/`stderr`.
- The poll loop at `ralph-workflow/ralph/process/manager/_managed_process.py:379-393`
  computes `inactivity_deadline = last_output_at_ref[0] + timeout`
  (`:381-383`) and arms the kill with `timeout_cause = "inactivity"` and
  `timeout_value = timeout` (`:391-393`); the terminate-then-raise is at
  `:394-405`.
- `EXEC_DEFAULT_TIMEOUT_MS = 90_000` (`ralph-workflow/ralph/timeout_defaults.py:135`)
  — a 90-second default inactivity cap. Historically, a CPU-busy command that never
  emits stdout (e.g. tight loop, in-process buffered logging, redirected
  output to a file) died at `timeout_ms` with cause `inactivity` and
  the structured error template `hard_cap=False` / `inactivity=True`
  prompted the agent to "raise `timeout_ms` up to the cap" — but raising
  it only widened the same output-only window.

Historically, this was the cause behind "CPU-busy exec dies even though the agent
raised `timeout_ms`". Under the job and liveness architecture, the fix
routes exec through the `ExecJobRegistry`, which decides every kill from
sampled liveness rather than from the output-only window. The output-only
rule survives only on the **injected-runner seam**
(`ralph-workflow/ralph/mcp/tools/exec.py:777-790`), which the production
entry path does not take — see "Cause 1 — present-day liveness subsystem"
for the current behavior.

### Cause 1 — present-day liveness subsystem (CURRENT)

Under the job and liveness architecture, the §3b fix layers a fuzzy liveness
assessor over the exec job flow. The historical output-only inactivity window
is no longer the kill rule for any production `exec` call, short or long: the
production path always starts a registry job and the registry decides kills
from sampled liveness.

- `ralph-workflow/ralph/mcp/tools/exec.py:792-821` is the production entry
  path. It resolves the registry (`:792-799`), calls
  `effective_registry.start(...)` unconditionally (`:809-816`), and then
  `effective_registry.wait(job_id, wait_ms=EXEC_SOFT_REPLY_BUDGET_MS, ...)`
  (`:817-822`). **Every** normal `exec` therefore becomes a registry job;
  none of them fall back to the output-only window.
- `ralph-workflow/ralph/mcp/tools/exec.py:777-790` is the **injected runner
  seam**, not a fallback for "short" calls. It is entered only when
  `effective_deps.runner is not None` or `run_command is not
  _DEFAULT_RUN_COMMAND` (`:777-779`) — i.e. when a caller injected a custom
  runner. Only on this seam does `run_command` reach
  `_run_subprocess` (`ralph/mcp/tools/exec.py:567`) and the
  `_managed_process.py` output-only inactivity window.
- A short in-request completion still returns the legacy output shape
  end-to-end — `exec.py:828-871` converts a terminal `JobResult` into the
  same `_CompletedProcessAdapter` the seam produced — so the *observable*
  output contract is unchanged. What changed is the kill rule, not the
  response shape.
- `ralph-workflow/ralph/process/manager/_liveness_assessor.py` —
  Sugeno weighted-average over six working signals (`o`, `c`,
  `io`, `t`, `n`, `w`; the per-signal helpers are at `:332-407` and the
  R1..R7 rule weights at `:409`); the verdict score is integrated by
  `StallIntegrator` (class at `:613`, `step` method at `:628`, leaky `threshold_(theta - s)/theta`) and a
  sustained-suspicion clock (`SuspicionClock`, imported from
  `ralph/process/manager/_suspicion_clock.py` at `:254` and re-exported at
  `:261`) that tracks R4/R7 streaks (`_SUSPICIOUS_RULES` at `:411`) for
  `EXEC_NO_PROGRESS_WINDOW_S` (15 min, `timeout_defaults.py:588`) →
  `no_progress`.
  Per-spec rule table (`:31-32`, `:474`): R1 z=1.0, R2 z=0.9, R3 z=0.9,
  R4 z=0.6, R5 z=0.0, R6 z=0.2 (fire-only, see docstring), R7 z=0.55.
- `ralph-workflow/ralph/process/manager/_liveness_sampler.py` —
  psutil-tree sampler (`TreeSampler` at `_liveness_sampler.py:321`) reads the process tree every
  `EXEC_LIVENESS_SAMPLE_INTERVAL_S` (2 s, `timeout_defaults.py:480`) over an
  `EXEC_LIVENESS_EVIDENCE_WINDOW_S` (30 s, `timeout_defaults.py:485`) window.
  Each signal is independently `None`-on-read-failure (D5 fail-closed, module
  docstring `:9` and the `_windowed_rate` rebaseline at `_liveness_sampler.py:246-282`); the typed
  adapter is local to the sampler because the existing
  `_PsutilProcessLike` protocol
  (`ralph/process/manager/_process_manager_types.py:27-38`) lacks
  `cpu_times`/`io_counters`/`num_ctx_switches`.
- `ralph-workflow/ralph/mcp/tools/_exec_jobs.py:58-60` — `ExecJobRegistry`
  (`class` at `:308`) runs the job on a sample thread that drives the
  assessor every cycle, accumulates stall pressure, and decides the kill
  cause. `KillCause` (`:58-60`) names six literals, but only five are
  actually emitted by the registry today: `abandoned` (`:541`), `hard_cap`
  (`:546`), `stalled` (`:586`), `no_progress` (`:590`), and `session_end`
  (`:840`, the `shutdown_all` default). `restart_lost` is declared and
  rendered by the error formatter
  (`ralph/mcp/tools/_exec_execution_error.py:243,251-263`) but has no
  producer in `_exec_jobs.py`: a restart destroys the in-memory registry, so
  the only observable restart contract is the non-retryable
  "unknown or expired job; process was killed or lost in server restart"
  error from `wait()`/`cancel()`
  (the literal at `_exec_jobs.py:771`; `_lookup_job_locked` is the only
  site that holds the message string — `_exec_jobs.py:742-772`,
  with the surrounding capability-mismatch path at `_exec_jobs.py:752-756`
  and `:764-767` and the `_build_job_result` consumer at `_exec_jobs.py:443-462`),
  which is covered by
  `tests/test_exec_jobs_session_lifecycle.py::test_restart_lost_fresh_registry_returns_nonretryable_lost_in_server_restart_error`
  (`tests/test_exec_jobs_session_lifecycle.py:108-162`, invented-id assertion at `:149-154`)
  and `tests/test_tool_exec_wait_cancel.py::test_exec_wait_for_restart_lost_job_id_returns_idempotent_nonretryable_error`.
  A short in-request completion lands in the registry as a regular
  `JobResult` and the legacy output shape is preserved end-to-end;
  only jobs that take longer than the soft-reply budget return a
  snapshot with a `job_id` and the "call exec_wait; do not re-run"
  instruction (`exec.py:828-840`, `_exec_jobs.py:805-812`).

## Cause 2 (CONFIRMED) — single 300-second absolute cap

Even after the operator raises `timeout_ms`, the wall clock is capped.

- `EXEC_MAX_TIMEOUT_MS = 300_000` (`ralph-workflow/ralph/timeout_defaults.py:142`)
  — 5 minutes. Larger values are rejected at the import-time invariant
  check below the constant.
- On the **injected-runner seam** only,
  `ralph-workflow/ralph/mcp/tools/exec.py:631-637` passes
  `hard_timeout=EXEC_MAX_TIMEOUT_MS / 1000` to
  `handle.communicate_and_cleanup`. The `inactivity` window is gated BELOW
  this; the `hard_cap` window is the outermost envelope. Note this is the
  seam path (`exec.py:777-790` → `exec.py:567` → `exec.py:603`), not the
  production entry path.
- On the **production entry path**, the wall-clock cap is the registry's
  job ceiling, not `EXEC_MAX_TIMEOUT_MS`:
  `ralph-workflow/ralph/mcp/tools/_exec_jobs.py:244-252` resolves the
  ceiling once per job (`ceiling_s = min(max_runtime_ms / 1000.0,
  EXEC_JOB_CEILING_MAX_S)` when the caller set `max_runtime_ms`, else
  `EXEC_JOB_CEILING_S` (`ralph/timeout_defaults.py:603`, 3600 s)) and stores
  it as `job.ceiling_ms`; `step_job` kills with `cause="hard_cap"` at
  `_exec_jobs.py:544-546` against that single value. `EXEC_JOB_CEILING_MAX_S = 14400.0`
  (`ralph/timeout_defaults.py:609`, 4 h) is the outer bound.
- **Historical (pre-§3b) wording**, quoted here because it is the text that
  used to ship: `ralph-workflow/ralph/mcp/tools/_exec_execution_error.py`
  `ExecutionError._render_timeout` (now `:187-197`, split into a job path and
  a legacy in-request path) rendered `timeout_cause == "hard_cap"` as:

  > "The command already ran to the EXEC_MAX_TIMEOUT_MS ceiling; a
  > larger `timeout_ms` cannot extend it. Split the work into smaller
  > commands. Background command execution is not supported; for running
  > jobs, call exec_wait to monitor or exec_cancel to stop."

  That text was wrong on the job path: jobs are capped by the job ceiling,
  not `EXEC_MAX_TIMEOUT_MS`. Today a job `hard_cap` renders
  `_exec_execution_error.py:295-312` ("Killed at the {ceiling}ms job runtime
  ceiling… raise `max_runtime_ms` instead") and the silence-only stall
  sentence survives only on the legacy in-request path
  (`_exec_execution_error.py:310-360`).

- **Resolved — the background path exists today.** The original finding
  was that every exec-family tool (`exec`, `unsafe_exec`, the
  iteration-timeboxed variants) had no `run_in_background` parameter and
  no daemon-style submit-and-poll, so "run it in the background" pointed
  at a path the server did not have. That was true pre-§3b and is now
  false: `exec_wait` and `exec_cancel` are registered tools
  (`ralph/mcp/tools/names.py:40-41`, exported at `:103-104`; handlers in
  `ralph/mcp/tools/_exec_job_tools.py:43-44` and `handle_exec_wait` at
  `:126`). A long call returns a `JobSnapshot` carrying a `job_id` and
  the `"instruction": "call exec_wait; do not re-run"` string
  (`ralph/mcp/tools/exec.py:828-840`), and `handle_exec_wait` clamps its
  `wait_ms` to `EXEC_SOFT_REPLY_BUDGET_MS`
  (`_exec_job_tools.py:139-143`). The hard-cap template no longer
  recommends a nonexistent path.

## Cause 3 (CONFIRMED) — client timeouts only configured for OpenCode (prior defect)

Prior to §3b, only `ralph-workflow/ralph/mcp/transport/opencode.py:131`
and `:148` wrote a client-side MCP request timeout. Every other
transport wrote only the endpoint URL — leaving the connection bound to
whatever default the client's MCP SDK ships with (Codex's 60-s SDK
default per
https://learn.chatgpt.com/docs/config-file/config-reference,
OpenCode's ~60-s SDK default per issue #8701, the Kimi `mcp` table's
60-s global `tool_timeout_ms`, the Claude Code `MCP_TOOL_TIMEOUT`
"about 28 hours" default, etc.), which the OpenCode issue thread shows
is the failing signal the operator sees as
`McpError: MCP error -32001: Request timed out` — the verbatim error
string quoted in issue #8701's body.

- `ralph-workflow/ralph/mcp/transport/opencode.py:41-45` carries the
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
default) saw the run as
`McpError: MCP error -32001: Request timed out` during a single
legitimately long exec call, with no way to extend it from Ralph.

The fix posture per client (line numbers in the working tree as of
the WU-3 citation pass):
- Claude: writes `timeout` (`ralph/mcp/transport/claude.py:80`).
- Kimi: writes `toolTimeoutMs` (`ralph/mcp/transport/kimi.py:119`).
- Codex: writes `tool_timeout_sec` (`ralph/mcp/transport/codex.py:227`).
- Pi: enforces `MCP_TOOL_CALL_DEADLINE_MS` in the generated bridge
  (`ralph/mcp/transport/pi.py:306`).
- OpenCode: writes both `timeout` and `experimental.mcp_timeout`
  (`ralph/mcp/transport/opencode.py:131` and `:148`).
- Cursor, AGY: cited docs page names no public per-server tool-call
  timeout config; Ralph writes only `{"url": endpoint}` /
  `{"serverUrl": endpoint}` (`cursor.py:88-90`, `agy.py:88-94`).
- Nanocoder: the package's docs DO name a `timeout` field, but it is
  the connection handshake / tool-listing deadline; tool-call
  enforcement reverts to the MCP SDK's hard-coded default. Ralph
  therefore writes only `{"transport": "http", "url": endpoint}`
  (`nanocoder.py:56-59`) — the row reads `UNKNOWN (docs say "SDK default
  60 s")` in the verified table.
- OpenCode, schema-drift follow-up (RESOLVED — no new write; see the
  verified table's OpenCode row): the published OpenCode config schema
  does mark `experimental.mcp_timeout` deprecated and does expose a
  non-deprecated `timeout.execution` at both `mcp.timeout.*` and
  `mcp.<server>.timeout.*`, and Ralph writes neither directly. That is
  not a Ralph-side gap. OpenCode's own config normalizer copies
  `experimental.mcp_timeout` into BOTH `timeout.catalog` and
  `timeout.execution`, and expands Ralph's per-server scalar through
  `migrateMcpTimeout` into `{ startup, catalog, execution }`, so the
  330000 ms Ralph writes is already the value OpenCode enforces on the
  tool-call deadline. Adding the global `mcp.timeout` would be a
  regression rather than a fix: it is a fill-in default merged per
  server as `{ ...timeout, ...server.timeout }`, so it would cap every
  operator-declared MCP server at ~5.5 minutes instead of OpenCode's
  12-hour default. Citations: the verified table's OpenCode row,
  https://opencode.ai/config.json, and
  https://github.com/anomalyco/opencode/blob/dev/packages/core/src/config/normalize.ts.

## Cause 4 (CONFIRMED) — streamed output does not keep clients alive

> **Status note.** The "no progress-token handling" evidence in this section
> is **HISTORICAL** — it describes the pre-§3b streaming surface and is kept
> so a reader can tell what the old failure mode was. Ralph DOES extract and
> propagate progress tokens today; the current behavior is recorded in
> "Cause 4 — present-day progress-notification path (CURRENT)" further on.

The streaming surface emitted only `notifications/message` frames — the
least-load-bearing notification the JSON-RPC 2.0 spec offers. There was no
progress-token handling, no `notifications/progress` emission, and no
`_meta.progressToken` propagation anywhere under
`ralph-workflow/ralph/mcp/server/`.

- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:228-235` builds
  every *output-chunk* notification as

  ```python
  notification = {"jsonrpc": "2.0", "method": "notifications/message", "params": event}
  ```

  and serialises it as `event: message` over SSE.

That meant a long exec call whose subprocess emits stdout every few
seconds did NOT extend the client-side MCP timer on any of the
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

**Unverified, and withdrawn as claims.** No cited page says whether
Kimi's or OpenCode's deadline is *extended* by streamed output. An
earlier revision of this section asserted that "Kimi's 60s default
ignores output entirely" and that "OpenCode's MCP SDK ignores output
unless a progress notification arrives". Neither sentence is backed by
any source this doc cites, so both are struck rather than softened.
What IS verified for those two clients is only that neither cited page
documents an output-conditioned extension; treat the mechanism as an
open question for a follow-up unit, not as an established fact.

The fix is orthogonal to the inactivity window (Cause 1) and the
absolute cap (Cause 2): progress notifications reach only clients that
honour them, and a client only counts them when the request carried a
`progressToken` round trip. When a client never sends one, the run
report still ends up "client abandoned, server running" — the state
Cause 5 makes observable.

### Cause 4 — present-day progress-notification path (CURRENT)

Under the job and liveness architecture, Ralph extracts the request's
progress token and emits `notifications/progress` frames bound to that
token. The historical "zero matches" claim no longer holds.

- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:62-89` —
  `extract_progress_token` reads the token from four request shapes:
  `params._meta.progressToken`, `params.progressToken`,
  `params.arguments._meta.progressToken`, and
  `params.arguments.progressToken`. A `str` or `int` token is returned;
  anything else yields `None` (no subscription).
- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:107-126` —
  `_build_progress_frame` renders the SSE frame and its payload:

  ```python
  notification = {
      "jsonrpc": "2.0",
      "method": "notifications/progress",
      "params": params,  # progressToken, progress, optional total
  }
  ```

- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:129-175` —
  `_install_progress_subscription` registers a request-scoped
  `ProgressSubscription` on the shared `ExecJobRegistry` via
  `registry.subscribe_progress`. Bindings are explicit: `exec_wait` /
  `exec_cancel` requests carry `job_id` and bind at install time
  (`:159-172`), while `exec` / `unsafe_exec` / `raw_exec` have no
  `job_id` until the worker calls `registry.start(...)`, so the
  subscription is bound afterwards by
  `ralph/mcp/tools/_exec_jobs.attach_progress_subscription`
  (`_exec_jobs.py:919-979`), which the exec handler hands the subscription
  to at `ralph/mcp/tools/exec.py:807-822`.
- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:241-244` wires
  both mechanisms into the streaming POST: the output-chunk sink at
  `:237` and the progress subscription at `:241-243`, published to the
  request thread through the `_CURRENT_PROGRESS_SUB` ContextVar
  (`:35-42`) so the exec handler can read it. Teardown at `:273-278`
  unsubscribes, so a torn-down sink never emits to a stale token.

A `grep` over `ralph-workflow/ralph/mcp/server/` for either
`progressToken` or `notifications/progress` now returns matches in
`exec_sse_streaming.py` (lines 66, 68, 69, 72, 73, 74, 77, 81, 82, 85,
86, 108, 115, 123, 196, 197) rather than zero. What remains true is the
narrower claim: output chunks still travel as `notifications/message`
(`exec_sse_streaming.py:228-235`), so a client that sends no
`progressToken` gains nothing from streamed stdout.

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
and CPU" complaint. Historically, the subprocess eventually finished
(or was reaped by the output-only inactivity window in Cause 1) but
the agent had no visibility into that — and the next iteration of the
watchdog saw an EXPLAIN-attached, half-finished process it could not
tie back to a specific request. Under the job and liveness architecture,
long jobs are registered in `ExecJobRegistry`, tracked via active liveness
monitoring, and reaped explicitly on termination or session shutdown.

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

- `ralph-workflow/ralph/mcp/server/lifecycle.py:2986` and `:3003` invoke
  `_spawn_mcp_process(...)`, which is defined at
  `ralph/mcp/server/lifecycle.py:3233-3408`
  and runs

  ```python
  [
      sys.executable, "-m", "ralph.mcp.server",
      "--host", "127.0.0.1",
      "--port", str(port),
      "--workspace", str(root),
  ]
  ```

  (`ralph/mcp/server/lifecycle.py:3422-3432`; the `"ralph.mcp.server"` module name is at
  `:3425`). The MCP server runs as a separate child
  process; the parent owns the lifecycle via
  `StandaloneMcpProcess.shutdown` (`_standalone_mcp_process.py:46-72`),
  which calls `self.process.terminate(grace_period_s=SHUTDOWN_TERMINATE_GRACE_S)`
  at line 55 and `self.process.wait(timeout=SHUTDOWN_REAP_WAIT_S)` at line 57.
  Both waits are module constants (`_standalone_mcp_process.py:20` and `:24`,
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

  - `ralph-workflow/ralph/mcp/server/runtime.py:914-950` —
    `exit_when_owner_exits` daemon watches the parent PID; when the
    owner is already gone at entry it calls `exit_process(0)`
    (default `os._exit`) at line 939, and the watcher thread does the
    same at line 946 once `get_parent_pid()` stops matching the
    recorded owner.
  - `ralph-workflow/ralph/mcp/server/runtime.py:953-1000` —
    `main(argv)` calls `install_shutdown_signal_handlers()` (`:983`),
    then `exit_when_owner_exits(os.environ)` (`:984`), parses args
    (`:985`), and enters `run_standalone_server` (`:986`) which blocks
    on the HTTP server until SIGTERM/Ctrl-C.
  - The `MCP_OWNER_PID_ENV` env var
    (`ralph/mcp/protocol/env.py:95`, `McpEnvVar.OWNER_PID` at `:39`)
    is the parent→child hand-off that lets the watcher know when
    to kill itself.
  - The owner-loss hook the registry installs is registered in
    `ralph/mcp/tools/_exec_jobs.py:347-350`
    (`shutdown_all("session_end")`), run by
    `_run_owner_loss_hooks` (`runtime.py:887-897`) on owner loss and by
    `_run_shutdown_hooks` (`runtime.py:880-884`) on SIGTERM
    (`runtime.py:903-908`) and at exit (`runtime.py:911`).

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

## Per-path termination matrix (CURRENT)

Each row names one way an `ExecJobRegistry`-managed job can be
terminated and records the calling mechanism, the working-tree
citations that prove the mechanism is wired in, and the bound the
producer can guarantee. The matrix is read together with the
kill-cause literals recorded under `Cause 1 — present-day liveness
subsystem (CURRENT)`: the five `KillCause` literals
(`abandoned`, `hard_cap`, `stalled`, `no_progress`, `session_end`)
are how the rows announce themselves to the caller; this matrix
records the seams that produce them.

| path | mechanism | citation (path:line) | proof-or-bound |
| --- | --- | --- | --- |
| SIGTERM of server | Signal handler fires `_run_shutdown_hooks`; registry hook installed at startup calls `registry.shutdown_all("session_end")`; `_kill_job` calls `ManagedProcess.terminate(grace_period_s=0.0)` which escalates to `os.killpg(pgid, SIGKILL)` (preceded by SIGTERM and the git cleanup window when the group contains git) over the worker's process group, taking worker + descendant in one group-kill. | `ralph/mcp/server/runtime.py:900-908` (handler install) → `ralph/mcp/server/runtime.py:880-884` (`_run_shutdown_hooks`) → `ralph/mcp/tools/_exec_jobs.py:347-350` (registry hook) → `ralph/mcp/tools/_exec_jobs.py:840-845` (`shutdown_all`) → `ralph/mcp/tools/_exec_jobs.py:477-495` (`_kill_job`) → `ralph/process/manager/_managed_process.py:562-578` (`terminate`) → `ralph/process/manager/_process_manager.py:1222-1290` (`cleanup_orphans` / `_kill_pgid`) | **Proof**: `tests/test_exec_job_shutdown_subprocess_e2e.py:120-161` (`test_sigterm_shutdown_kills_worker_and_descendant`) — worker + descendant reaped, `kill_cause == "session_end"`. Bound: the entire shutdown completes within `SHUTDOWN_TERMINATE_GRACE_S + SHUTDOWN_REAP_WAIT_S = 10s`. |
| Owner SIGKILL with server surviving | `exit_when_owner_exits` daemon watches `os.getppid()`; when it stops matching the recorded owner, it invokes `_run_owner_loss_hooks` (a daemon thread plus a bounded join at `EXEC_OWNER_LOSS_HOOK_BUDGET_S`), the registry hook fires `registry.shutdown_all("session_end")`, and the same group-kill path as SIGTERM completes the tree. The same sequence fires immediately at entry when the stored owner PID never matched the current PPID (already-orphaned case). | `ralph/mcp/server/runtime.py:914-950` (`exit_when_owner_exits`) → `ralph/mcp/server/runtime.py:887-897` (`_run_owner_loss_hooks`) → immediate fire at `ralph/mcp/server/runtime.py:938`, watcher fire at `ralph/mcp/server/runtime.py:945` → registry hook at `ralph/mcp/tools/_exec_jobs.py:347-350` → group-kill same as the row above. | **Proof**: `tests/test_exec_job_shutdown_subprocess_e2e.py:164-251` (`test_owner_loss_watcher_kills_worker_and_descendant`) — owner SIGKILLed, child server's owner watcher fires the pre-exit hook, worker + descendant reaped. Bound: the watch loop polls at the configured sleep interval (`sleep(1.0)` at `ralph/mcp/server/runtime.py:944`, bound to `time.sleep` as the production default at `ralph/mcp/server/runtime.py:918`); the hook thread is bounded by `EXEC_OWNER_LOSS_HOOK_BUDGET_S`. |
| MCP server SIGKILLed (Linux) | No Python hook runs on SIGKILL — atexit and `atexit.register(_run_shutdown_hooks)` (`runtime.py:911`) are skipped and no signal handler can fire. Tree death depends on the kernel: on Linux, off-main-thread exec-job spawns (the production HTTP dispatch path) route through the supervisor process `ralph/process/manager/_supervisor.py` (`start_new_session=True`), which arms `PR_SET_PDEATHSIG=SIGTERM` against the server holder and runs an `os.killpg(pgid, SIGKILL)` sweep over the process group on holder death, while the forked worker child arms `PR_SET_PDEATHSIG=SIGKILL` against the supervisor; main-thread spawns install `_install_pdeathsig` as the ProcessManager preexec (`prctl(PR_SET_PDEATHSIG, SIGKILL)`). | `ralph/process/manager/_supervisor.py:203-229` (the `killpg` PG sweep in `_kill_tree_and_exit`) and `ralph/process/manager/_supervisor.py:263-329` (`run`, the supervisor lifecycle) — `ralph/process/manager/__init__.py:128-155` (`_supervisor_invocation`) — `ralph/process/manager/__init__.py:173-223` (`_default_sync_process_factory`, supervisor routing at `:186-221`) — `ralph/process/manager/__init__.py:96-112` (`_install_pdeathsig`) and `ralph/process/manager/__init__.py:263-290` (`_default_async_process_factory`). | **Proof**: `tests/test_exec_job_shutdown_subprocess_e2e.py:367-603` (`test_worker_thread_sigkill_kills_exec_job_tree_and_its_descendant`, marked `subprocess_e2e`, Linux-only) — starts registry via worker thread, forks descendant, SIGKILLs holder, and asserts both worker and descendant die; additionally `tests/test_exec_job_shutdown_subprocess_e2e.py:270-365` (`test_parent_sigkill_kills_exec_job_via_pr_set_pdeathsig`) for direct main-thread preexec. Bound: sub-millisecond kernel delivery plus supervisor `killpg` sweep. |
| MCP server SIGKILLed (non-Linux) | SIGKILL cannot run any Python hook on any platform — no atexit, no signal handler, no watchdog. On Linux the supervisor and preexec close the gap (see Linux row above); on macOS/Windows no `PR_SET_PDEATHSIG` exists, the gating `sys.platform.startswith("linux")` test short-circuits supervisor and preexec routing, and the PDEATHSIG test skips. **Nothing in-process bounds the lifetime of a job orphaned by a server SIGKILL on these platforms**: once the server is dead, its `ExecJobRegistry`, liveness sampler threads, the abandonment timer (`EXEC_JOB_ABANDON_S` lives in the now-dead process), and all signal/atexit hooks are gone with it. The orphan's worker and descendants are re-parented to PID 1 and run to natural completion; OS-level process cleanup alone does not terminate them. | `ralph/process/manager/__init__.py:186` (sync supervisor/preexec gate) and `ralph/process/manager/__init__.py:274-279` (async preexec gate — the `_death_preexec(command) if sys.platform.startswith("linux") and threading.current_thread() is threading.main_thread() else None` ternary inside `_default_async_process_factory`) — **pre-SIGKILL caps that DO NOT bind a SIGKILLed-server orphan**: `ralph/mcp/tools/_exec_jobs.py:336-341` (`_running_jobs` capped at `EXEC_MAX_RUNNING_JOBS=4` prevents new submissions from leaking past the cap while the server is alive; `_finished_results` is a `deque(maxlen=EXEC_JOB_RESULT_FIFO=16)`); constants at `ralph/timeout_defaults.py:622`, `:629`, and the abandonment window `EXEC_JOB_ABANDON_S = 120.0` at `ralph/timeout_defaults.py:617` (does NOT fire on an orphan whose server is dead). | **Stated, not proven.** No non-Linux test exists; `tests/test_exec_job_shutdown_subprocess_e2e.py:271-289` skips with the reason "PR_SET_PDEATHSIG is Linux-only and no cross-platform parent-death equivalent is available; once the MCP server is SIGKILLed on a non-Linux platform there is no in-process lifetime bound for an orphaned exec-job tree." **Today's actual bound on the orphan: NONE in-process after the server is SIGKILLed.** The pre-SIGKILL caps cited above prevent new submissions from leaking past the cap while the server is alive; they do NOT cause an in-flight SIGKILLed-server orphan to exit. `EXEC_JOB_ABANDON_S` lives in the (now-dead) server process. The orphan's lifetime is therefore bound by: (a) the orphan runs to natural completion, (b) OS-level cleanup when it exits, or (c) an external supervisor outside this codebase. **Pre-SIGKILL caps retained only for completeness**, not as a bound on the orphan itself. |
| Post-restart `exec_wait` on a lost job | The in-memory registry is destroyed when the server restarts, so any `exec_wait(old_job_id)` falls through the lookup and raises a non-retryable "unknown or expired job; process was killed or lost in server restart" error. It never fabricates a snapshot or a result. | `ralph/mcp/tools/_exec_jobs.py:742-772` (`_lookup_job_locked`; the literal message string at `ralph/mcp/tools/_exec_jobs.py:771`) | **Proof**: `tests/test_exec_jobs_session_lifecycle.py:108-162` (`test_restart_lost_fresh_registry_returns_nonretryable_lost_in_server_restart_error`) — the `:149-154` branch asserts the same string for an invented, never-seen `job_id`, proving the new registry cannot distinguish prior-server ids from any other id (the correct behaviour). Bound: immediate. |
| Normal job completion | The registry runner polls the handle each `EXEC_LIVENESS_SAMPLE_INTERVAL_S` (2 s); a non-`None` returncode marks the job finished with `kill_cause=None`, then `_close_job_streams` calls `pm.cleanup_orphans(handle)` to kill descendants that outlived the root and closes the managed pipes. The job slides from the running map to the bounded results FIFO. | `ralph/mcp/tools/_exec_jobs.py:532-537` (poll-and-mark) — `ralph/mcp/tools/_exec_jobs.py:497-513` (`_mark_finished`) — `ralph/mcp/tools/_exec_jobs.py:431-441` (`_close_job_streams` → `pm.cleanup_orphans`) — `ralph/process/manager/_process_manager.py:1222-1290` (`cleanup_orphans` / `_kill_pgid`) | **Proof**: orphan sweep locked by `tests/test_exec_jobs.py:522-539` (`test_trace_1_root_exit_with_orphaned_child`) and `tests/test_exec_jobs.py:182-202` (`_TrackingProcessManager.cleanup_orphans`). Bound: `EXEC_LIVENESS_SAMPLE_INTERVAL_S = 2.0` (`ralph/timeout_defaults.py:480`) is the worst-case staleness between root exit and the registry noticing. |
| Session end | `session_end` is the `KillCause` literal stamped onto the `JobResult` whenever `shutdown_all()` fires; the trigger is whichever path delivered the teardown — SIGTERM via `install_shutdown_signal_handlers`, owner SIGKILL via `exit_when_owner_exits`, or the lifecycle's `shutdown_mcp_server(bridge)` teardown, which calls `bridge.shutdown()` and triggers the child server's own SIGTERM-handler chain. There is no separate "session end" Python mechanism; it is the union of those paths. | kill-cause literal at `ralph/mcp/tools/_exec_jobs.py:840` (also rendered as `KillCause` member of the literal block at `ralph/mcp/tools/_exec_jobs.py:58-60`); lifecycle teardown at `ralph/mcp/server/lifecycle.py:3554-3526` (`shutdown_mcp_server`) → `ralph/mcp/server/_standalone_mcp_process.py:46-72` (`StandaloneMcpProcess.shutdown`); constants at `ralph/mcp/server/_standalone_mcp_process.py:20` and `:24` (5.0 s each) | **Proof**: `tests/test_exec_job_shutdown_subprocess_e2e.py:120-161` reads `CAUSE session_end` off the registry's `wait()` output at `:129`, proving the literal renders through the SIGTERM path. Bound: teardown wall-clock ≤ `SHUTDOWN_TERMINATE_GRACE_S + SHUTDOWN_REAP_WAIT_S = 10s`. |

The matrix is the single point of truth for the seaming. A code edit
that changes any of the cited lines without updating this table is a
drift edit; the per-row proof-or-bound column is what the §3b fix's
"kills the tree" claims have to back up against.

## Deviations (PENDING-RECONCILIATION)

PA-001 escalation: criteria 3 and 6 are PENDING-RECONCILIATION with external product authority.
Prior to this fix the scenario-8 / scenario-13 effective kill times were
unreconciled against the spec's "killed at `stall_window` after activity stops"
wording, and the spec-internal score / kill-time monotonicity stood as an open
conflict. Both findings are proven mathematical limits of the spec's D2/D3
architecture awaiting authoritative product reconciliation, recorded on
`path:line` evidence, not decided or accepted by developer authority.

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
  `ralph-workflow/tests/test_exec_liveness_scenarios.py:319`) —
  `burst_stop = 30`, kill at **168 s** (continuous 167.5 s)
- scenario 13 ("mid-run deadlock",
  `ralph-workflow/tests/test_exec_liveness_scenarios.py:644`) —
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
`ralph-workflow/tests/test_liveness_pa002_properties.py:475-549`
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
to satisfy strict monotonicity). The evidence cites that back this analysis —
`tests/test_liveness_assessor.py`,
`tests/test_liveness_pa002_properties.py`,
`tests/test_liveness_properties.py`, and the
`Cause 1 — present-day liveness subsystem (CURRENT)` rule table — are
preserved unchanged to support the product reconciliation process.

## Appendix: how to verify the doc is still truthful

The five causes are stable as long as the file:line citations in this
doc match the working tree. Drift is the only way a citation goes
stale, and the fastest drift check is a re-read of:

- `ralph-workflow/ralph/timeout_defaults.py:135` (`EXEC_DEFAULT_TIMEOUT_MS`)
  and `:142` (`EXEC_MAX_TIMEOUT_MS`), plus `:598`
  (`EXEC_SOFT_REPLY_BUDGET_MS`), `:603` (`EXEC_JOB_CEILING_S`), `:609`
  (`EXEC_JOB_CEILING_MAX_S`) — the Cause 1 / Cause 2 constants.
- `ralph-workflow/ralph/process/manager/_managed_process.py:315` (the
  output-only `last_output_at_ref` reset) and `:379-405` (the poll
  loop that computes `inactivity_deadline` and arms the kill)
- `ralph-workflow/ralph/mcp/tools/exec.py:777-790` (the injected-runner
  seam) and `:792-821` (the production registry entry path) — reading
  these two ranges is how a reader tells whether the output-only window
  is still reachable for normal calls.
- `ralph-workflow/ralph/mcp/tools/_exec_jobs.py:244-252` (the registry
  `hard_cap` ceiling resolution), `:544-546` (the kill check against
  `job.ceiling_ms`) and `:58-60` (the six kill causes)
- `ralph-workflow/ralph/mcp/transport/opencode.py:131,148` (Cause 3
  per-server + experimental writes; the comment block at lines 41-45
  is the issue-thread reference) and `:175` (the `$schema` value the
  OpenCode schema-drift claim in the verified table is read against)
- `ralph-workflow/ralph/mcp/transport/claude.py:80`,
  `ralph-workflow/ralph/mcp/transport/codex.py:227`,
  `ralph-workflow/ralph/mcp/transport/kimi.py:119`,
  `ralph-workflow/ralph/mcp/transport/pi.py:306`
  (the rest of Cause 3's per-transport writes)
- `ralph-workflow/ralph/mcp/server/exec_sse_streaming.py:62-89`
  (`extract_progress_token`), `:107-126` (`_build_progress_frame`
  emitting `notifications/progress`) and `:228-235` (the historical
  `notifications/message` output path) — Cause 4
- `ralph-workflow/ralph/mcp/server/_saturated_dispatch.py:161-181`
  (Cause 5b)
- `ralph-workflow/ralph/mcp/server/runtime.py:914-950`
  (`exit_when_owner_exits`) and `:953-1000` (`main`) — the Cause 5c seam
- `ralph-workflow/ralph/mcp/server/lifecycle.py:2986` and `:3003` (the
  two `_spawn_mcp_process` call sites), `:3233-3408` (its definition) and
  `:3392-3402` (the `sys.executable -m ralph.mcp.server` argv list)
- `ralph-workflow/ralph/mcp/server/_standalone_mcp_process.py:20` and
  `:24` (`SHUTDOWN_TERMINATE_GRACE_S` / `SHUTDOWN_REAP_WAIT_S`, 5.0 s each)
- `ralph-workflow/ralph/mcp/server/_timing_safety.py:84`
  (`TIMING_SAFETY_TRANSPORT_HEADROOM_MS`) and `:91-93`
  (`TIMING_SAFETY_TRANSPORT_FLOOR_MS` — the per-transport floor the
  §3b fix's written keys must clear)

The URL citations in the verified client-timeout table are also
subject to drift — every URL listed there was fetched live in the
WU-5 pass, then re-opened one at a time in the WU-3 citation pass,
where the quoted string was read back off the live page before the row
was allowed to stand. Re-running
`scripts/fabrication_guard.py --level 2
ralph-workflow/docs/agents/exec-long-running-root-cause.md`
after this doc is edited is the cheapest way to confirm none of
them have since 404'd — but note what level 2 does and does not
prove. It resolves the GitHub repos through the API and HEAD-checks
markdown-link URLs; it never opens prose, so a vendor page that
silently renames or re-defines the field it documents still passes
level 2 with exit 0. Reading the quoted text back off the live page is
the only check that catches that class of drift, and it is what the
WU-3 pass did — that is how the OpenCode `experimental.mcp_timeout`
deprecation in the verified table was found. A row that reads
`UNKNOWN` is not a defect; it is the doc being honest about what the
cited docs page does and does not name, and an `Unverified` clause
inside a row is the same honesty at smaller grain.

If any of the file:line citations above move, this doc must update
before the fix the doc references is merged. The §3b fix agents use
this doc as a target; an out-of-date citation is the same as a
missing citation.
