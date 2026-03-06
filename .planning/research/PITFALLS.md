# Pitfalls Research

**Domain:** GLM-agent planning workflow systems for coding tasks
**Researched:** 2026-03-05
**Confidence:** MEDIUM-HIGH

## Critical Pitfalls

### Pitfall 1: Treating plans as free-form prose instead of machine-checked contracts

**What goes wrong:**
Planner outputs look detailed but are not reliably parseable; downstream phases misread steps, skip constraints, or execute out of order.

**Why it happens:**
Teams optimize for readability in demos and skip schema-first outputs plus validation/retry loops.

**How to avoid:**
Require JSON-mode outputs for planning artifacts, validate every artifact against strict schemas, and hard-fail on invalid structure instead of "best effort" parsing.

**Warning signs:**
- Frequent parser fallbacks (regex/manual parsing)
- Planner output contains phase names not in your roadmap schema
- Same prompt produces structurally different plan shapes

**Phase to address:**
Phase 1 - Artifact schema and contract design (before orchestration work).

---

### Pitfall 2: Assuming "OpenAI-compatible" means behavior-identical

**What goes wrong:**
Workflows pass smoke tests but break in production because model defaults/edge behavior differ (sampling constraints, endpoint-specific extras, partial feature gaps).

**Why it happens:**
Teams migrate by only swapping `base_url` and model name, then assume parity across planning, tool-use, and failure handling.

**How to avoid:**
Create a compatibility test matrix for every critical workflow primitive (tool calls, streaming, structured outputs, retry semantics, stop conditions). Treat compatibility as "API shape compatible, behavior verified per feature."

**Warning signs:**
- "Works with provider A, flaky with GLM" bugs
- Increased malformed tool arguments after migration
- Hidden assumptions like `temperature=0` behavior copied from non-GLM systems

**Phase to address:**
Phase 0 - Provider compatibility test harness during project bootstrap.

---

### Pitfall 3: Losing reasoning continuity in interleaved tool workflows (GLM-specific)

**What goes wrong:**
Agent quality degrades after tool calls; planner appears inconsistent across turns and repeats earlier mistakes.

**Why it happens:**
In GLM thinking modes, teams do not preserve and return `reasoning_content` exactly as required between turns/tool results.

**How to avoid:**
Implement a strict message-roundtrip adapter that stores and replays unmodified reasoning content whenever interleaved/preserved thinking is enabled. Add regression tests for tool-call turn chaining.

**Warning signs:**
- First-turn plan is coherent but follow-up plan revisions become erratic
- Cache hit rates drop unexpectedly in long coding sessions
- Tool result handling quality drops after each additional call

**Phase to address:**
Phase 2 - Agent runtime/message adapter implementation.

---

### Pitfall 4: No durable execution state (checkpoint/resume)

**What goes wrong:**
Long planning workflows restart from scratch after transient failures; partial outputs are lost; duplicate actions occur.

**Why it happens:**
Teams prototype with in-memory state and never add thread/run checkpointing semantics.

**How to avoid:**
Make checkpointing a first-class requirement: stable run IDs, persisted state snapshots after each phase gate, and resumable execution from last verified artifact.

**Warning signs:**
- "Please rerun everything" as standard recovery
- Duplicate artifacts for the same phase after retries
- Inability to explain exact state at failure time

**Phase to address:**
Phase 2 - Orchestration core (state machine, persistence, resume).

---

### Pitfall 5: Missing explicit stop/termination conditions

**What goes wrong:**
Planner-reviewer loops continue indefinitely, burning budget and creating contradictory revisions.

**Why it happens:**
Workflow design relies on model self-termination ("it will stop when done") instead of hard limits and explicit done criteria.

**How to avoid:**
Define layered termination: max turns/messages, explicit approval tokens, and budget/time ceilings. Enforce in orchestrator, not prompt text.

**Warning signs:**
- Iteration counts drift upward release over release
- High token spend on "polish" loops with no acceptance delta
- Plans oscillate between two styles without convergence

**Phase to address:**
Phase 2 - Orchestration policy and budget controls.

---

### Pitfall 6: Ignoring concurrency and rate-limit physics

**What goes wrong:**
Subagent fan-out causes throttling storms (1302) or service overload retries (1305), producing cascading delays and partial plans.

**Why it happens:**
Teams design for ideal parallelism, not provider-specific concurrency quotas and peak-hour dynamics.

**How to avoid:**
Use adaptive concurrency pools per model tier, queue backpressure, exponential backoff with jitter, and phase-aware load shedding (defer non-critical branches).

**Warning signs:**
- Repeated 1302/1305 spikes during business-hour runs
- Retry queues grow faster than completion throughput
- "Random" timeout failures only under multi-project load

**Phase to address:**
Phase 3 - Reliability hardening and load policy.

---

### Pitfall 7: Context-window blindness in planning chains

**What goes wrong:**
Important constraints or early decisions silently drop from context; later phases violate requirements and traceability.

**Why it happens:**
Teams underestimate cumulative tokens from instructions + tool traces + reasoning + artifacts, and do not track truncation risk.

**How to avoid:**
Track prompt budget per phase, summarize with loss-aware reducers, pin non-droppable constraints separately, and use tokenizer checks/cached stable prefixes.

**Warning signs:**
- Late-phase plans contradict early constraints
- Increasing hallucinated file paths/phase IDs in long sessions
- Token usage near context ceiling before completion

**Phase to address:**
Phase 1 and Phase 2 - Prompt architecture first, then runtime token governance.

---

### Pitfall 8: No guardrails around tool side effects

**What goes wrong:**
Agent performs unsafe file or command actions during planning (not execution), causing accidental repository mutations or security exposure.

**Why it happens:**
Planning agents are granted broad tools "for flexibility" without operation classes, approval gates, or scoped permissions.

**How to avoid:**
Classify tools by risk (read-only, reversible-write, destructive), require human/automated policy approval for risky classes, and enforce per-phase tool allowlists.

**Warning signs:**
- Planning stage produces unexpected file modifications
- Tool logs show write/delete commands from research/planning phases
- Security reviews find prompt-injection paths into high-privilege tools

**Phase to address:**
Phase 0 and Phase 3 - Tool policy model early; enforcement and auditing before scale-up.

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Parse markdown plans with regex instead of schemas | Faster MVP demo | Fragile routing, frequent manual fixes | Never for multi-phase automation |
| Global mutable conversation state across projects | Simple implementation | Cross-project leakage, non-reproducible runs | Only single-project throwaway prototype |
| "Retry forever" on provider errors | Appears robust initially | Cost blowups and queue collapse under load | Never |
| Disable validation for speed | Fewer initial failures | Silent corruption of artifacts | Never |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| GLM OpenAI-compatible endpoint | Swap URL only and skip behavior tests | Run compatibility suite per primitive (tools, JSON mode, streaming, thinking) |
| GLM thinking + tools | Drop or rewrite `reasoning_content` between turns | Replay exact reasoning chain when enabled |
| Multi-agent framework (LangGraph/AutoGen/OpenAI Agents) | Rely on default loop behavior | Enforce explicit termination, checkpoints, and guardrails at orchestrator level |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Unbounded subagent fan-out | 1302/1305 spikes, retry storms | Adaptive concurrency + queue backpressure | Usually at 2+ concurrent projects or peak traffic |
| Full-history reprompt every turn | Latency and token costs climb per step | Hierarchical memory and summary reducers | Around long planning sessions with 20+ turns |
| Replanning from scratch on every failure | Slow recovery, duplicated work | Checkpoint/resume by phase and artifact | Any non-trivial workflow with external tool calls |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Granting planning agents destructive shell/file tools | Repo damage, secrets leakage | Phase-specific least-privilege tool allowlists |
| Executing tool arguments without validation | Injection into shell/database/file paths | Strict argument schema validation and sanitization |
| Missing approval gates for high-impact operations | Accidental irreversible actions | Human/policy checkpoints before destructive classes |

## "Looks Done But Isn't" Checklist

- [ ] **Structured artifacts:** Every planning artifact validates against schema on first pass or deterministic retry.
- [ ] **Termination safety:** Every loop has max-steps, budget ceiling, and explicit done signal.
- [ ] **Resume safety:** Killing the process mid-phase can resume from the latest checkpoint without duplication.
- [ ] **Provider parity:** Compatibility tests pass on GLM target models, not only baseline OpenAI models.
- [ ] **Context integrity:** Non-droppable constraints persist across full workflow runs.
- [ ] **Tool safety:** Planning phases cannot execute destructive operations without explicit gate.

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Free-form artifacts | Phase 1 (Schema contracts) | 100% schema validation pass rate in CI fixtures |
| OpenAI-compat parity assumption | Phase 0 (Provider harness) | Cross-provider conformance suite green |
| Lost GLM reasoning continuity | Phase 2 (Runtime adapter) | Interleaved-thinking regression tests stable |
| Missing checkpoint/resume | Phase 2 (State core) | Chaos kill/resume test recovers without duplicate artifacts |
| Infinite planner loops | Phase 2 (Loop policy) | Budget/turn caps enforced in integration tests |
| Rate-limit collapse | Phase 3 (Reliability hardening) | Load tests show bounded retries and queue depth |
| Context-window blindness | Phase 1 + 2 (Prompt + token governance) | Long-run test preserves critical constraints |
| Unsafe tool side effects | Phase 0 + 3 (Policy + enforcement) | Audit log shows blocked unauthorized operations |

## Sources

- Zhipu BigModel docs - OpenAI compatibility warning and setup (`https://docs.bigmodel.cn/cn/guide/develop/openai/introduction.md`) [MEDIUM: official doc]
- Zhipu BigModel docs - Thinking modes, interleaved/preserved reasoning requirements (`https://docs.bigmodel.cn/cn/guide/capabilities/thinking-mode.md`) [HIGH: official GLM behavior guidance]
- Zhipu BigModel docs - Structured output / JSON mode (`https://docs.bigmodel.cn/cn/guide/capabilities/struct-output.md`) [HIGH]
- Zhipu BigModel docs - Function calling safety notes (`https://docs.bigmodel.cn/cn/guide/capabilities/function-calling.md`) [HIGH]
- Zhipu BigModel docs - Rate limits and error codes 1302/1305 (`https://docs.bigmodel.cn/cn/api/rate-limit.md`) [HIGH]
- Zhipu BigModel docs - Context window truncation and cache usage (`https://docs.bigmodel.cn/cn/guide/start/introduction.md`, `https://docs.bigmodel.cn/cn/guide/capabilities/cache.md`) [HIGH]
- LangGraph docs via Context7 - checkpointing, interrupts, durable execution (`/langchain-ai/langgraph`) [HIGH]
- AutoGen docs via Context7 - max message and text mention termination (`/microsoft/autogen`) [HIGH]
- OpenAI Agents SDK docs via Context7 - guardrails, handoffs, tracing (`/openai/openai-agents-python`) [HIGH]

---
*Pitfalls research for: GLM-agent planning workflow systems*
*Researched: 2026-03-05*
