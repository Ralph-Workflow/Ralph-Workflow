# RFC-011: Revised Post-Mortem - MCP Transport Lifetime Coupled to Agent Attempt Lifetime

**RFC Number**: RFC-011  
**Title**: Revised Post-Mortem - MCP Transport Lifetime Coupled to Agent Attempt Lifetime  
**Status**: Superseded / Previous conclusion was incorrect  
**Author**: OpenCode / Hephaestus  
**Created**: 2026-04-10  
**Revised**: 2026-04-10

---

## Abstract

The previous post-mortem claimed the incident was resolved by unifying prompt tool names, harness allowlists, and runtime advertisement around a session-scoped tool manifest. That conclusion was wrong.

Those changes fixed a real consistency problem, but they did **not** fix the production failure. The deeper root cause is architectural: Ralph ties MCP transport lifetime to **individual agent attempts** instead of to the **overall run**. That makes provider startup, MCP connection, retry/fallback behavior, and artifact submission depend on fragile per-attempt socket churn.

The correct target architecture is a **stable run-scoped MCP server** with **per-session/per-agent capability enforcement inside the server**, not a new server/socket for every retry or fallback attempt. That target must be transport-abstract rather than Unix-only: stable endpoint first, platform-specific transport second.

---

## What the old post-mortem got wrong

The earlier document concluded:

- the bug class was fixed in code,
- the session-scoped manifest was authoritative,
- the runtime/tool/prompt drift issue was resolved.

That is false as a complete explanation.

Live runtime evidence collected after that document showed:

1. `ralph --generate-commit` still failed in production-like usage.
2. Claude-family providers still exposed zero Ralph tools to the model.
3. OpenCode runs still exposed native tools instead of Ralph’s brokered MCP surface.
4. The `ralph` MCP server could appear in provider init state as **failed**.
5. No artifact was actually submitted to Ralph in the failing runs.

So the “implemented” conclusion was premature and materially incorrect.

---

## Actual incident summary

### User-visible symptoms

- `ralph --generate-commit` stalled or churned through multiple fallback models.
- Agents often analyzed the diff correctly but emitted plain-text/JSON output instead of submitting an artifact.
- Providers reported that the expected Ralph MCP tool was unavailable or missing.
- Commit generation moved to the next model instead of first forcing the same agent to resubmit correctly.

### Impact

- 20+ follow-up changes produced partial local correctness without eliminating the real runtime failure.
- Test green-ness became untrustworthy because the tests were validating internal consistency rather than provider-truth behavior.
- Retry/fallback churn obscured the primary defect by constantly recreating transport state.

---

## Actual root cause

## 1. Transport lifetime was coupled to agent-attempt lifetime

The current architecture creates a new `SessionBridge` for each agent attempt:

- commit plumbing creates a fresh bridge per fallback attempt,
- reducer-driven invocation creates a fresh bridge per agent invocation,
- each bridge creates a fresh Unix socket endpoint,
- that endpoint is torn down when the attempt/bridge ends.

This means Ralph’s MCP transport is **not stable** across:

- same-agent retries,
- fallback agents,
- sequential phases,
- provider startup delays.

This is the core architectural defect.

### Why this is bad

Provider startup timing is not under Ralph’s control. A provider may:

- start the model session immediately,
- load user settings/plugins/skills first,
- resolve MCP configs later,
- connect to custom MCP servers only seconds after process launch.

When transport lifetime is shorter than provider startup + MCP initialization time, the provider is racing a moving target.

---

## 2. Provider-truth validation was missing at the actual boundary

We had many tests for:

- artifact dispatch,
- tool manifests,
- prompt rendering,
- socket behavior,
- harness config generation,
- dispatch-time capability enforcement.

But we did **not** have the one test that actually mattered:

> For a real provider CLI in non-interactive mode, against a live Ralph MCP endpoint, does the model actually see and call the tool we told it to use?

That missing seam allowed internal fixes to look correct while the provider-facing contract was still broken.

---

## 3. Commit fallback behavior hid the real defect

When no commit artifact was ingested, the commit chain advanced to the next model too quickly.

That caused two bad effects:

- it blurred “submission failed” into “model failed,”
- it tore down and recreated transport state repeatedly.

Same-agent resubmission should have happened before agent switching.

---

## Live evidence gathered after the original post-mortem

### Evidence A: Prompt can be correct while provider-visible tool set is still wrong

The archived prompts were corrected to use Claude-callable tool names such as:

- `mcp__ralph__ralph_submit_artifact`

But a live Claude probe still returned `TOOL_MISSING`.

### Evidence B: Live socket can exist while Claude still exposes zero Ralph tools

A direct live probe was run while Ralph was actively executing commit generation:

- the session-specific socket path existed,
- the exact generated `settings.local.json` and `mcp.json` were used,
- Claude still returned `TOOL_MISSING`.

This proves the issue is not just “the socket file doesn’t exist.”

### Evidence C: Claude init state showed `ralph` MCP server status = `failed`

Live provider init output showed:

- `mcp_servers`: included `ralph`
- `ralph.status = failed`
- no Ralph tools appeared in the model-visible tool list

So the provider did load the server entry, but the server handshake failed.

### Evidence D: `ralph --mcp-proxy` failed with `Connection refused`

Provider debug logs showed:

- the proxy attempted to connect to the session socket,
- the connection failed with `Connection refused (os error 61)`,
- the proxy gave up after a tiny retry budget.

### Evidence E: the proxy retry budget was absurdly small

`ralph --mcp-proxy` currently retries only:

- `5 attempts × 100ms sleep = 500ms total retry budget`

That is nowhere near robust enough for real provider startup and MCP config loading.

---

## Why the earlier “manifest fix” was only a partial fix

The earlier changes were still valuable:

- prompt tool names were made more coherent,
- harness allowlists were derived from capability manifest,
- `tools/list` respected the tool filter,
- hidden vs nonexistent tool semantics improved.

But those changes only fixed **description drift**.

They did **not** fix **transport-lifetime drift**.

In other words:

- the old fix improved what Ralph *said* was available,
- but not whether the provider could reliably connect to a stable Ralph MCP server long enough to use it.

That is why the issue survived.

---

## Corrected architecture target

Ralph should move toward this model:

### Stable run-scoped MCP transport

- Ralph starts one MCP server when the run starts.
- That server stays alive for the entire Ralph run.
- Transport is not recreated between retries, fallbacks, or phases.
- Transport should not be hardcoded to `unix://`; the abstraction should support Unix sockets on Unix and a Windows-safe transport such as localhost TCP or named pipes. The most practical default is a localhost server bound to a random port at run start, with that concrete endpoint injected dynamically into each agent invocation.

### Session-aware policy inside the server

- planning sessions get planning-safe capabilities,
- development sessions get write-capable behavior,
- reviewer sessions get review-safe behavior,
- commit sessions get commit-safe behavior.

But those differences are enforced **inside the server** via session identity and capability checks, not by spinning up a separate transport per attempt.

### Same-agent resubmission before fallback

If the model produced the content but failed to submit the artifact:

- force same-agent resubmission first,
- only fallback after that fails.

### Provider-truth smoke tests

The repo needs an executable seam that proves:

1. real provider CLI starts,
2. real Ralph MCP server is visible and connected,
3. expected tool is present in model-visible tool list,
4. tool call succeeds,
5. Ralph ingests the artifact.

Without that seam, future “fixes” will keep validating the wrong layer.

---

## Corrective actions

### Immediate

1. Stop claiming the bug class is fully resolved.
2. Update the post-mortem to reflect the actual architecture defect.
3. Add submission-aware same-agent retry before fallback.
4. Increase MCP proxy retry budget to tolerate real provider startup.

### Structural

1. Introduce a run-scoped shared MCP server abstraction.
2. Move per-session/per-agent capability enforcement into server-side routing.
3. Remove per-attempt transport churn from commit plumbing.
4. Refactor reducer invocation to reuse stable transport.

### Verification

1. Add provider-boundary smoke tests.
2. Add run-scoped transport lifetime tests.
3. Add explicit checks that `ralph` is connected, not merely configured.

---

## Architecture lessons

### 1. Internal consistency is not runtime correctness

A correct manifest, prompt, and allowlist still do not prove provider-visible tools are correct.

### 2. Transport lifetime should be longer than provider startup lifetime

If providers connect asynchronously, transport must be stable across that window.

### 3. Session isolation should not require multiplying servers

Transport can be stable while policy remains session-specific.

### 4. Fallback should not be the first response to submission failure

Fallback churn can destroy the exact state needed to debug and recover.

---

## Final revised conclusion

The real bug is not just “MCP tool visibility drift.”

The deeper architecture problem is:

> Ralph destroys and recreates MCP transport at the same granularity as agent attempts, while providers initialize MCP on their own slower lifecycle.

That is why this incident survived so many partial fixes.

The correct long-term fix is:

> one stable run-scoped MCP server, with session-aware policy inside it, and provider-truth contract tests at the real boundary.

---

## Implementation notes (added after Wave 3 completion)

The implemented architecture follows the corrected target with one clarification:

**Runtime transport is TCP loopback-only.** The MCP server binds to a TCP loopback endpoint (for example `tcp://127.0.0.1:<port>`) and this endpoint is the only supported runtime transport. Unix socket paths exist only in explicit negative test cases that verify `unix://` endpoints are correctly rejected at the boundary. The `TcpLoopbackTransport` symbol name reflects the actual behavior and avoids unix-oriented terminology in runtime paths.

**Commit boundary is orchestrator-owned.** The MCP server `submit_artifact` tool is the only commit-relevant operation exposed at the MCP boundary. The orchestrator reads the submitted artifact and performs the actual `git commit` via `Effect::CreateCommit`. MCP-side git write operations are denied by the policy gate in commit mode.

**Policy matrix enforced server-side.** The `commit` drain maps to a read-only + artifact-submission-only policy. Dev and fixer drains remain read-write capable. All enforcement happens inside `mcp-server` via the pre-dispatch capability check, not via provider-side prompt conventions.
