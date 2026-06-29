# Sisyphus System Analysis: Learnings for Ralph Workflow

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


## Executive Summary

This document analyzes the Sisyphus/oh-my-openagent system to identify patterns applicable to the Ralph Workflow unattended pipeline. Key areas of transfer: **planning discipline**, **unattended execution**, **agent orchestration**, and **verification strategies**.

**Date**: 2026-04-07
**Source**: `/Users/mistlight/Projects/oh-my-openagent`
**Status**: Analysis Complete

---

## Part I: What Sisyphus Does Well

### 1. Agent Architecture

Sisyphus uses a **team-of-specialists** model with clear role delineation:

| Agent | Role | Model | Trigger |
|-------|------|-------|---------|
| **Sisyphus** | Main orchestrator | opus/kimi/glm | Always active |
| **Hephaestus** | Deep autonomous worker | gpt-5.4 | Complex implementation |
| **Prometheus** | Strategic planner | opus/kimi/glm | `/start-work` |
| **Oracle** | Architecture/debugging consultant | varies | Architecture decisions, debugging |
| **Librarian** | Docs/code search | varies | External library questions |
| **Explore** | Fast codebase grep | varies | Internal code discovery |
| **Metis** | Plan reviewer | varies | Pre-plan consultation |

**Delegation Model**: Sisyphus delegates by **category**, not model. Categories map to optimal models automatically:

```typescript
// Category → Model mapping (abstracted)
visual-engineering → frontend-optimized model
deep → autonomous research + execution
quick → single-file, low effort
ultrabrain → hard logic, architecture
```

### 2. Planning Discipline (Prometheus)

Prometheus implements **two-phase planning**:

#### Phase 1: Interview Mode
- Classifies intent before planning (Trivial/Simple, Refactoring, Build from Scratch, Mid-sized, Collaborative, Architecture, Research)
- Uses **lightweight for simple, heavyweight for complex** scaling
- Fires explore/librarian agents **before asking questions** for "Build from Scratch" and "Architecture" intents
- Maintains a **draft file** (`.sisyphus/drafts/{slug}.md`) updated after every meaningful exchange

#### Phase 2: Plan Generation
Triggered by:
- AUTO: When clearance check passes
- EXPLICIT: User says "Create the work plan"

**Mandatory sequence** (tracked via TodoWrite):
1. Consult Metis for gap analysis
2. Generate work plan to `.sisyphus/plans/{name}.md`
3. Self-review: classify gaps (critical/minor/ambiguous)
4. Present summary with auto-resolved items
5. Ask about high accuracy mode (Momus review)
6. If high accuracy: Submit to Momus, iterate until OKAY
7. Delete draft, guide user to `/start-work {name}`

### 3. Plan Template Structure

Prometheus plans include:

```
## TL;DR
> Quick summary + deliverables + effort estimate + parallel execution strategy

## Context
### Original Request
### Interview Summary
### Metis Review (gaps addressed)

## Work Objectives
### Core Objective
### Concrete Deliverables
### Definition of Done
### Must Have / Must NOT Have (Guardrails)

## Verification Strategy
> ZERO HUMAN INTERVENTION - ALL verification is agent-executed
> Frontend: Playwright, CLI: tmux, API: curl, Library: REPL

## Execution Strategy
### Parallel Execution Waves
> Wave 1 (foundation) → Wave 2 (core) → Wave 3 (integration) → FINAL (4 parallel reviews)
> Each wave completes before next begins
> Target: 5-8 tasks per wave

## TODOs (per task)
- What to do / Must NOT do
- Recommended Agent Profile + Skills
- Parallelization (wave, blocks, blocked_by)
- References (pattern, API/type, test, external)
- Acceptance Criteria
- QA Scenarios (MANDATORY: happy path + failure case)
- Commit strategy

## Final Verification Wave (4 parallel reviews)
- F1: Plan Compliance Audit (oracle)
- F2: Code Quality Review (unspecified-high)
- F3: Real Manual QA (unspecified-high + playwright)
- F4: Scope Fidelity Check (deep)

## Success Criteria
```

### 4. Ralph Loop (Unattended Pipeline)

Ralph Loop enables **fully unattended execution** through a state machine:

#### State Machine (`RalphLoopState`)
```typescript
interface RalphLoopState {
  active: boolean
  iteration: number
  max_iterations?: number
  message_count_at_start?: number
  completion_promise: string  // Detection trigger
  initial_completion_promise?: string
  verification_attempt_id?: string
  verification_session_id?: string
  started_at: string
  prompt: string
  session_id?: string
  ultrawork?: boolean
  verification_pending?: boolean
  strategy?: "reset" | "continue"
}
```

#### Event Handling
- Listens for `session.idle` events
- Checks completion via:
  1. **Transcript file detection** (faster, file-based)
  2. **API message inspection** (slower, API-based)
- On idle without completion: injects continuation prompt

#### Continuation Prompts
```typescript
// Standard continuation
`Your previous attempt did not output completion promise. Continue working.
Original task: {{PROMPT}}
When FULLY complete, output: <promise>{{PROMISE}}</promise>`

// Ultrawork verification (Oracle check before completion)
`You emitted <promise>...</promise>. This does NOT finish the loop yet.
REQUIRED NOW:
- Call Oracle to verify task is actually complete
- Include original task in Oracle request
- If Oracle does not verify, continue fixing`
```

#### Two Continuation Strategies
| Strategy | Behavior |
|----------|----------|
| **continue** | Inject prompt in same session |
| **reset** | Create new session, inherit context |

#### Session Recovery
- Detects orphaned state from deleted sessions
- Marks sessions as "in recovery" to avoid race conditions
- Handles `session.error` events

### 5. Todo Enforcement

Sisyphus uses **aggressive todo enforcement** to prevent task abandonment:

```typescript
// IMMEDIATELY upon trigger detection - NO EXCEPTIONS
todoWrite([
  { id: "plan-1", content: "Consult Metis", status: "pending", priority: "high" },
  { id: "plan-2", content: "Generate work plan", status: "pending", priority: "high" },
  // ...
])
```

Key enforcement mechanisms:
- TodoWrite on every phase transition
- System yanks agent back if it goes idle
- Status must be explicitly marked (pending → in_progress → completed)

### 6. Skill-Embedded MCPs

Skills carry their own MCP servers, spun up on-demand:

```
Skill definition includes:
├── Domain-tuned system instructions
├── Embedded MCP server config
└── Scoped permissions

Execution: Skill loaded → MCP server starts → Task runs → MCP server stops
```

Benefit: Context window stays clean. MCP servers don't pollute global context.

### 7. Hash-Anchored Edits

Addresses "harness problem" - edit tool failures blamed on models:

```
11#VK| function hello() {
22#XJ|   return "world";
33#MB| }
```

Every line tagged with content hash. Edit references `LINE#ID`. If content changed, hash mismatch → edit rejected. Result: Grok Code Fast success rate 6.7% → 68.3%.

---

## Part II: Applicability to Ralph Workflow

### What We Have

Ralph Workflow currently implements:
- Pipeline phases (spin, build, review, commit)
- Event/reducer architecture
- Iteration/fallback logic
- Completion detection via `completion_promise` pattern
- Session-based state management

### Gaps Identified

| Sisyphus Feature | Ralph Gap |
|------------------|-----------|
| Prometheus planning (interview + structured plan) | Missing structured planning phase |
| Parallel execution waves | Sequential phase execution |
| Metis (pre-plan review) | No pre-execution review |
| Momus (post-plan review) | No post-plan verification |
| Agent-executed QA scenarios | Verification is human-dependent |
| Todo enforcement (yank on idle) | No idle detection + recovery |
| Skills with embedded MCPs | No skill-MCP integration |
| Hash-anchored edits | Standard edit tool |
| Session recovery | Partial implementation |
| Draft management during planning | No draft file concept |

---

## Part III: Recommended Adoptions

### High Priority (Fundamental to Unattended Operation)

#### 1. Sisyphus Execution Phases

From `sisyphus/default.ts` - The core orchestration loop:

```
Phase 0: Intent Gate
  → Classify: research | implementation | investigation | evaluation | fix | open-ended
  → Verbalize routing decision

Phase 1: Codebase Assessment (open-ended tasks)
  → Quick assessment: config files, sample 2-3 similar files
  → State classification: Disciplined | Transitional | Legacy/Chaotic | Greenfield

Phase 2A: Exploration & Research
  → Parallel agent firing (explore/librarian with run_in_background=true)
  → Background result collection via system-reminder
  → Continue only with non-overlapping work

Phase 2B: Implementation
  → Pre-implementation: create todos, mark in_progress
  → Delegation with 6-section prompt structure:
    - TASK: Atomic, specific goal
    - EXPECTED OUTCOME: Concrete deliverables with success criteria
    - REQUIRED TOOLS: Explicit tool whitelist
    - MUST DO: Exhaustive requirements
    - MUST NOT DO: Forbidden actions
    - CONTEXT: File paths, existing patterns, constraints
  → Verification: lsp_diagnostics, build, tests

Phase 2C: Failure Recovery
  → 3 consecutive failures → STOP, REVERT, DOCUMENT, CONSULT Oracle, ASK USER

Phase 3: Completion
  → Evidence requirements: todos done, diagnostics clean, build passes
```

**Sisyphus-Junior Workflow** (subagent executor):
- Execute directly (no delegation - BLOCKED_TOOLS = ["task"])
- Todo/Task obsession: 2+ steps → create todos immediately
- Verification: lsp_diagnostics clean, build passes, todos completed
- Termination: STOP after first successful verification, max 2 status checks

#### 2. Structured Planning Phase

**Current**: Direct implementation upon user request.

**Adopt**: Prometheus-style two-phase planning:

```
Phase 0: Intent Classification
├─ Trivial (< 10 lines, obvious) → Skip planning, execute
├─ Simple (1-2 files, < 30 min) → Lightweight interview
└─ Complex (3+ files, architectural) → Full Prometheus workflow

Phase 1: Interview Mode
├─ Explore codebase for patterns
├─ Librarian for external knowledge
├─ Draft file updated continuously
└─ Metis consultation before plan

Phase 2: Plan Generation
├─ Structured plan to .ralph/plans/{name}.md
├─ Parallel execution waves
├─ Per-task QA scenarios
└─ Final verification wave
```

**Files to create/modify**:
- `src/agents/prometheus/` → `src/agents/planner/`
- Add interview-mode intent classification
- Add plan template

#### 3. Todo Enforcement System

**Current**: Loose todo tracking.

**Adopt**: Aggressive enforcement:

```typescript
// On idle detection (session.idle event)
if (agent_goes_idle_without_progress) {
  // System yanks back
  inject_continuation_prompt(remainder_of_task)
  mark_current_todo_in_progress()
}
```

**Implementation path**:
- Add idle detection to event loop
- Add continuation prompt injection
- Track todo state per iteration

#### 4. Parallel Execution Waves

**Current**: Sequential phase execution (spin → build → review → commit).

**Adopt**: Wave-based parallelization:

```
Wave 1 (Foundation - parallel):
├── Task 1: Project scaffolding [quick]
├── Task 2: Type definitions [quick]
└── Task 3: Core interfaces [quick]

Wave 2 (Core - parallel):
├── Task 4: Primary implementation [deep]
├── Task 5: Secondary implementation [unspecified-high]
└── Task 6: Integration logic [deep]

Wave 3 (Integration - parallel):
├── Task 7: API endpoints [unspecified-high]
├── Task 8: UI components [visual]
└── Task 9: Telemetry [unspecified-high]

Wave FINAL (4 parallel reviews):
├── F1: Plan compliance [oracle]
├── F2: Code quality [unspecified-high]
├── F3: QA execution [unspecified-high]
└── F4: Scope fidelity [deep]
```

**Implementation path**:
- Extend pipeline state to track wave execution
- Add dependency matrix parsing
- Add wave-based task dispatch

### Medium Priority (Enhances Quality)

#### 4. Agent-Executed QA Scenarios

**Current**: Human-dependent verification.

**Adopt**: QA scenarios per task:

```typescript
// Per-task QA template
{
  task_id: "auth-login",
  qa_scenarios: [
    {
      name: "happy_path_valid_credentials",
      tool: "Bash (curl)",
      preconditions: "Server running on port 3000",
      steps: [
        "curl -X POST http://localhost:3000/api/login",
        "-d '{\"email\":\"test@example.com\",\"password\":\"valid123\"}'",
        "-H 'Content-Type: application/json'"
      ],
      expected: "HTTP 200, response contains {\"token\":\"...\"}",
      evidence: ".ralph/evidence/auth-login-happy.json"
    },
    {
      name: "failure_invalid_credentials",
      tool: "Bash (curl)",
      preconditions: "Server running on port 3000",
      steps: [
        "curl -X POST http://localhost:3000/api/login",
        "-d '{\"email\":\"test@example.com\",\"password\":\"wrong\"}'",
        "-H 'Content-Type: application/json'"
      ],
      expected: "HTTP 401, response contains {\"error\":\"Invalid credentials\"}",
      evidence: ".ralph/evidence/auth-login-failure.json"
    }
  ]
}
```

#### 5. Metis/Momus Review System

**Metis** (pre-plan): Catches gaps before planning
**Momus** (post-plan): Verifies plan quality

```typescript
// Metis consultation (before plan)
task(subagent_type="metis", prompt=`
  Review this planning session before I generate the work plan:
  User's Goal: {summary}
  What We Discussed: {key points}
  My Understanding: {interpretation}
  Research Findings: {discoveries}
  
  Identify:
  1. Questions I should have asked but didn't
  2. Guardrails that need to be explicitly set
  3. Potential scope creep areas
  4. Assumptions needing validation
  5. Missing acceptance criteria
`)
```

#### 6. Ultrawork Verification Mode

Add Oracle verification before claiming completion:

```
Loop iteration completes
├─ Agent outputs: <promise>task complete</promise>
├─ System: This does NOT finish the loop yet
├─ REQUIRED: Call Oracle to verify
│   └─ "Is the task actually complete?"
├─ Oracle: Reviews skeptically
│   └─ Output: <promise>VERIFIED</promise> or <promise>INCOMPLETE: reason</promise>
├─ If INCOMPLETE: Continue fixing
└─ If VERIFIED: Loop complete
```

### Lower Priority (Nice to Have)

#### 7. Skill-Embedded MCPs

Skills carry MCP server definitions:

```yaml
# Skill definition example
name: playwright
description: Browser automation
mcp_server:
  command: npx
  args: ["-y", "@playwright/mcp"]
  env:
    BROWSER_HEADLESS: "true"
```

#### 8. Hash-Anchored Edits

Content-hash per line for edit validation. See `oh-my-openagent` hashline implementation.

#### 9. Draft File Management

During interview mode, maintain `.ralph/drafts/{topic}.md` updated continuously.

---

## Part IV: Implementation Roadmap

### Phase 1: Foundation (Minimal Viable Unattended)
- [ ] Add structured plan template
- [ ] Add intent classification
- [ ] Add todo enforcement on idle
- [ ] Add continuation prompt injection

### Phase 2: Quality (Parallel + Verification)
- [ ] Add parallel execution waves
- [ ] Add per-task QA scenarios
- [ ] Add Metis pre-plan consultation
- [ ] Add Momus post-plan review

### Phase 3: Advanced (Full Sisyphus Parity)
- [ ] Add Ultrawork verification mode
- [ ] Add skill-embedded MCPs
- [ ] Add hash-anchored edits
- [ ] Add draft management

---

## Part V: Specific Code Patterns

### Continuation Prompt Template
```rust
// Ralph equivalent of RalphLoop continuation
const CONTINUATION_PROMPT = r#"
RALPH LOOP [{{ITERATION}}/{{MAX}}]

Your previous attempt did not output the completion promise.
Continue working on the task.

Original task:
{{PROMPT}}

IMPORTANT:
- Review your progress so far
- Continue from where you left off
- When FULLY complete, output: <promise>{{PROMISE}}</promise>
- Do not stop until the task is truly done
"#;
```

### State Machine Transitions
```rust
enum RalphState {
    Planning,
    Executing { wave: u32, task: u32 },
    Verifying,
    Complete,
    Failed { reason: String },
}

// Transitions
Planning → Executing: Plan generated, user approved
Executing → Verifying: All waves complete
Verifying → Complete: Oracle verified
Verifying → Executing: Verification failed, continue
Executing → Failed: Max iterations reached
```

### Todo Enforcement Hook
```rust
// On session.idle event
fn handle_idle(ctx: &PluginContext, session_id: &str) {
    let state = read_pipeline_state();
    
    if state.current_task_incomplete() {
        // Yank back - inject continuation
        inject_continuation(ctx, session_id, state.remaining_work());
        mark_todo_in_progress(state.current_todo());
    }
}
```

---

## Appendix B: Detailed Patterns (from codebase exploration)

### Error Handling: Exponential Backoff

From `todo-continuation-enforcer/idle-event.ts`:

```typescript
const MAX_CONSECUTIVE_FAILURES = 10
const FAILURE_RESET_WINDOW_MS = 5 * 60 * 1000  // 5 minutes

// Reset after recovery window
if (state.consecutiveFailures >= MAX_CONSECUTIVE_FAILURES
    && Date.now() - state.lastInjectedAt >= FAILURE_RESET_WINDOW_MS) {
  state.consecutiveFailures = 0
}

// Exponential cooldown: 1x, 2x, 4x, 8x, 16x, 32x (capped at 32x)
const effectiveCooldown = CONTINUATION_COOLDOWN_MS * Math.pow(2, Math.min(state.consecutiveFailures, 5))
```

### Delegation Check Pattern (MANDATORY)

From `sisyphus/default.ts`:

```typescript
**Delegation Check (MANDATORY before acting directly):**
1. Is there a specialized agent that perfectly matches this request?
2. If not, is there a `task` category best describes this task?
3. Can I do it myself for the best result, FOR SURE?

**Default Bias: DELEGATE. WORK YOURSELF ONLY WHEN IT IS SUPER SIMPLE.**
```

### Session ID Continuity Pattern

From `sisyphus/default.ts`:

```typescript
**ALWAYS continue when:**
- Task failed/incomplete → `session_id="{session_id}", prompt="Fix: {specific error}"`
- Follow-up question on result → `session_id="{session_id}", prompt="Also: {question}"`
- Multi-turn with same agent → `session_id="{session_id}"` - NEVER start fresh
- Verification failed → `session_id="{session_id}", prompt="Failed verification: {error}. Fix."`

// WRONG: Starting fresh loses all context
task(category="quick", ..., prompt="Fix the type error...")

// CORRECT: Resume preserves everything
task(session_id="ses_abc123", ..., prompt="Fix: Type error on line 42")
```

### Failure Recovery: 3-Failure Rule

From `sisyphus/default.ts`:

```typescript
### After 3 Consecutive Failures:
1. **STOP** all further edits immediately
2. **REVERT** to last known working state (git checkout / undo edits)
3. **DOCUMENT** what was attempted and what failed
4. **CONSULT** Oracle with full failure context
5. If Oracle cannot resolve → **ASK USER** before proceeding
```

### Parallel Agent Launch Pattern

```typescript
// CORRECT: Always background, always parallel
task(subagent_type="explore", run_in_background=true, ...)
task(subagent_type="librarian", run_in_background=true, ...)

// Background Result Collection:
1. Launch parallel agents → receive task_ids
2. Continue only with non-overlapping work
3. **END YOUR RESPONSE.** The system will send `<system-reminder>` when tasks complete.
4. On receiving `<system-reminder>` → collect results via `background_output(task_id="...")`
5. NEVER call `background_output` before receiving `<system-reminder>`
```

### Continuation Prompt Injection

From `boulder-continuation-injector.ts`:

```typescript
await ctx.client.session.promptAsync({
  path: { id: sessionID },
  body: {
    agent: continuationAgent,
    ...(launchModel ? { model: launchModel } : {}),
    parts: [createInternalAgentTextPart(prompt)],
  },
  query: { directory: ctx.directory },
})
```

### Intent Classification (Interview Mode)

From `interview-mode.ts`:

| Intent | Strategy |
|--------|----------|
| Trivial/Simple | Tiki-Taka - fast turnaround, don't over-interview |
| Refactoring | Safety focus - understand behavior, test coverage |
| Build from Scratch | Discovery focus - explore patterns, then clarify |
| Mid-sized | Boundary focus - deliverables, exclusions, guardrails |
| Collaborative | Dialogue focus - explore together, no rush |
| Architecture | Strategic focus - Oracle consultation REQUIRED |
| Research | Investigation focus - parallel probes, synthesis |

---

## Appendix: Key File Mappings

| Sisyphus File | Ralph Equivalent | Purpose |
|---------------|-----------------|---------|
| `ralph-loop-hook.ts` | `pipeline/executor.rs` | Main loop entry |
| `loop-state-controller.ts` | `pipeline/state.rs` | State transitions |
| `ralph-loop-event-handler.ts` | `pipeline/event_handler.rs` | Event processing |
| `completion-handler.ts` | `pipeline/completion.rs` | Completion detection |
| `continuation-prompt-builder.ts` | `pipeline/continuation.rs` | Prompt injection |
| `completion-promise-detector.ts` | `pipeline/detection.rs` | Promise detection |
| `plan-generation.ts` | `planner/generator.rs` | Plan creation |
| `plan-template.ts` | `planner/template.rs` | Plan structure |
| `interview-mode.ts` | `planner/interview.rs` | Intent classification |
| `session-reset-strategy.ts` | `pipeline/session.rs` | Session management |
| `sisyphus/default.ts` | - | Task management, delegation patterns |
| `todo-continuation-enforcer/idle-event.ts` | - | Todo enforcement, exponential backoff |
| `boulder-state/storage.ts` | - | Plan/state persistence |
| `boulder-continuation-injector.ts` | - | Context injection for continuation |
| `dynamic-agent-core-sections.ts` | - | Agent categories, delegation table |

---

## References

- Sisyphus source: `/Users/mistlight/Projects/oh-my-openagent/src/hooks/ralph-loop/`
- Prometheus planner: `/Users/mistlight/Projects/oh-my-openagent/src/agents/prometheus/`
- Plan template: `src/agents/prometheus/plan-template.ts`
- Interview mode: `src/agents/prometheus/interview-mode.ts`
- Continuation prompts: `src/hooks/ralph-loop/continuation-prompt-builder.ts`
