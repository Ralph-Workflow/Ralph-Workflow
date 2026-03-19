# ORCHESTRATOR INSTRUCTIONS

⚠️ **RE-READ THIS FILE BEFORE EVERY ACTION. DO NOT WORK FROM MEMORY.**

⚠️ **YOU DISPATCH AGENTS WITH THE `task` PLUGIN. NOT AD-HOC PROSE.**

⚠️ **ALL AGENTS RUN IN BACKGROUND: `run_in_background=true`. NO EXCEPTIONS.**

⚠️ **RE-READ THE FP STYLE GUIDES BEFORE EVERY DISPATCH BATCH:**
- `docs/code-style/functional-transformations.md`
- `docs/code-style/boundaries.md`
- `docs/code-style/coding-patterns.md`

These guides change what "correct" looks like. If you work from memory, you will miss violations.

---

## 🚨 STYLE COMPLIANCE: SCAN EVERY AGENT RESULT IN STEP 4
* YOU MAY BE ASKED TO MONITOR ONCE A WHILE IF YOU DO, USE THESE AS CRITERIA TO ENSURE AGENT STAYS ON TRACK

When you read an agent result, scan its text for these red-flag phrases. If found, the agent has gone off-track — mark it `partial` or `blocked`, rerun the script, and relaunch with an explicit correction naming the phrase you found.

| Red-Flag Phrase | What It Signals |
|---|---|
| `#[allow(` | Forbidden lint suppression — must use `#[expect(..., reason = "...")]` only for proc-macro output |
| `simplified` / `simplified for now` / `workaround` / `quick fix` | Complexity avoidance instead of correct fix |
| `too complex` / `complexity avoided` / `removed for now` | Agent is avoiding the hard work |
| `deferred` / `will handle later` / `out of scope` | Agent skipped assigned work |
| `pre-existing` / `was already broken` | Using pre-existing as an excuse (forbidden per AGENTS.md) |
| `can't fix` / `unable to fix` without a detailed blocker | Agent gave up without proper escalation |
| `TODO` / `FIXME` left in changed files | Incomplete fix passed off as done |
| `cargo check` / `cargo build` / `cargo test` / `cargo clippy` | Agent ran cargo mid-work — forbidden, see below |
| only fixed compilation error and stopped | Agent treated compilation fix as complete work — must continue to dylint fixes |
| reading 3+ files before any edit | Agent is in a reading loop — tell it to stop and start editing |

If you are able to: Check if their current work is working against the style guide, you should nudge them with style guide links
if they are working against functional programming practices.

---

## 🚨 HARD BAN: NEVER USE `category` IN task() — ONLY subagent_type FROM opencode.json

**This project uses opencode.json to define specific cheap subagents. Category-based dispatch uses expensive external models and is STRICTLY FORBIDDEN.**

### The Rule
- **ONLY** use `subagent_type=` with agent names explicitly defined in `.opencode/opencode.json`
- **NEVER** use `category=` parameter in any `task()` call — ever
- **NEVER** use `subagent_type="Sisyphus-Junior"` — not a valid agent type
- Do NOT combine `category=` and `subagent_type=` — category takes precedence and ignores your subagent

### Valid subagent_type values (from opencode.json):
`build`, `workflow-gui`, `workflow-core`, `workflow-reducer`, `workflow-execution`, `workflow-io`, `workflow-workspace`, `workflow-git`, `workflow-config`, `workflow-app`, `workflow-logging`, `workflow-monitoring`, `workflow-misc`, `workflow-future`, `workflow-agents`, `workflow-prompts`, `workflow-json`, `workflow-cloud`, `test-helpers`, `xtask`, `workflow-tests`, `workflow-lints`, `workflow-docs`, `explore`, `librarian`, `oracle`, `metis`, `momus`
(plus `-cargo` variants of all workflow-* agents)

### If no agent covers the target path:
**STOP. Ask the user which agent to use. Do NOT fall back to category-based dispatch.**

### Module → Agent mapping:
| Path | Correct Agent |
|------|--------------|
| `src/phases/**` | `workflow-reducer` |
| `src/reducer/**` | `workflow-reducer` |
| `src/pipeline/**` | `workflow-reducer` |
| `src/checkpoint/**` | `workflow-reducer` |
| `src/app/**` | `workflow-app` |
| `src/git_helpers/**` | `workflow-git` |
| `src/json_parser/**` | `workflow-json` |
| `src/executor/**` | `workflow-execution` |
| `src/files/**` | `workflow-io` |
| `src/workspace/**` | `workflow-workspace` |
| `src/logging/**`, `src/logger/**` | `workflow-logging` |
| `src/monitoring/**` | `workflow-monitoring` |
| `src/common/**`, `src/templates/**`, `src/rendering/**` | `workflow-misc` |
| `src/prompts/**`, `src/guidelines/**` | `workflow-prompts` |
| `src/*.rs` (root level) | `workflow-core` |

---

## 🚫 CARGO IS INTENTIONALLY DISABLED FOR AGENTS

**Cargo is turned OFF for agents during refactoring. This is by design.**

Agents must NEVER run any cargo command during their work:
- `cargo build`, `cargo check`, `cargo test`, `cargo clippy` — ALL FORBIDDEN
- `.opencode/verify_agent_work.sh` — FORBIDDEN
- Any shell command that invokes cargo — FORBIDDEN

**Why:** Cargo is disabled so agents can perform LARGE architectural refactors without distraction. Compilation errors from refactoring are EXPECTED and OK. The orchestrator handles verification after all agents finish.

**If you catch an agent running cargo:** Immediately tell it:
> "Cargo has been intentionally disabled to allow for large refactors without distraction. Do not run cargo. Continue editing files."

**Every agent dispatch prompt MUST include the cargo-disabled box** (the template already includes it, but verify).

---

## 📋 TWO-PHASE APPROACH: REFACTOR FIRST, THEN COMPILE

### Phase 1: Refactoring (current phase)
- Fix ALL dylint violations first across all modules
- Compilation errors from refactoring are EXPECTED and OK
- Do NOT run the dispatch script until ALL refactoring is done
- Keep resending agents until every module's style violations are addressed

### Phase 2: Compilation Fixes (only after Phase 1 is complete)
- Fix all compilation errors introduced by the refactoring
- **CRITICAL: Compilation fixes must ALSO follow the style guide**
- When fixing a compilation error, agents must NOT introduce:
  - `let mut` bindings in domain code
  - `for`/`while` loops in domain code
  - `.push()`, `.insert()` on mutable receivers in domain code
  - Interior mutability (`Mutex`, `RefCell`, `LazyLock`) outside boundary modules
- If fixing a compilation error would require violating the style guide → restructure the type/API instead
- API changes from Phase 1 (e.g. `string_pool` now returns tuples) must be propagated with functional call-site patterns, not quick hacks

**NEVER run the dispatch script until you are confident all dylint violations have been addressed.**

### Phase 2 Compilation Review (MANDATORY after EVERY agent result)

⚠️ **RE-READ `docs/code-style/functional-transformations.md` AND `docs/code-style/boundaries.md` BEFORE reviewing any result.**

After collecting each agent's result via `background_output`, scan the result text for these red flags BEFORE re-running the script or launching more agents:

| Red Flag in Agent Output | Violation | Action |
|---|---|---|
| `let mut` in a non-boundary file | Style violation | Relaunch as fresh agent with explicit correction |
| `for` / `while` loop in domain code | Style violation | Relaunch as fresh agent |
| `.push(` / `.insert(` on mutable receiver in domain code | Style violation | Relaunch |
| `RefCell` / `Mutex` / `LazyLock` in domain code | Style violation | Relaunch |
| `#[allow(` anywhere | Lint suppression | Relaunch |
| `simplified` / `workaround` / `quick fix` | Avoidance | Relaunch |
| `cargo check` / `cargo build` / `cargo test` | Cargo forbidden | Tell agent: "Cargo is disabled. Continue editing files only." |
| `TODO` / `FIXME` left in changed code | Incomplete | Relaunch to finish |

If ANY red flag is found: mark agent `partial`, rerun the script, relaunch as a fresh worker with explicit statement of what was wrong.

**The orchestrator is responsible for catching style violations that agents introduce. Do not let bad fixes accumulate.**

---

## 🔄 FRESH SESSIONS ONLY — NEVER REUSE session_id

**Always start fresh. Never pass `session_id` to `task()`.**

Reusing sessions causes context pollution — the agent inherits stale context and old mistakes. This leads to:
- Agents re-doing work already done
- Agents confused about what state the codebase is in
- Compilation errors from stale module paths

**Rule:** Every dispatch is a fresh `task(subagent_type=..., run_in_background=true, ...)` with NO `session_id`. If an agent's session expired or task_id is gone from the registry, launch a new agent with a prompt that explains what was done and what remains.

---

## ⚡ AGENT BEHAVIOR MONITORING

### Signs an agent is doing real refactoring work (good):
- Writing/editing multiple files per 5-minute window
- Converting `let mut` + loops to iterator chains
- Creating new boundary modules (`io.rs`, `runtime.rs`, `boundary.rs`)
- Moving files to flatten nested boundary directories
- Extracting pure domain functions from boundary functions

### Signs an agent has gone off-track (requires immediate intervention):
- **Only fixed one compilation error and stopped** → resend with "compilation fix done, now do the actual refactoring"
- **Reading 3+ files before any edit** → tell it: "Stop reading. Start editing NOW."
- **Running cargo mid-work** → tell it: "Cargo is intentionally disabled. Do not run cargo."
- **Session timed out after 30+ minutes** → launch fresh agent explaining what was done
- **Agent exceeded 400 tool calls** → fresh agent with tighter, more surgical prompt
- **Analyzing architecture for 10+ messages without editing** → "Stop analyzing. Make the edit."

### Resend policy:
When an agent completes without finishing all its assigned work → **immediately resend as a fresh agent** with:
1. What was already done (so it doesn't redo it)
2. What specific work remains
3. Exact file/line references if known
4. Clear instruction to start editing immediately
5. If large architectural are needed, spell out the exact kind of large change they need to make, ensure they do not avoid the hard work

---

## How You Dispatch (Read This First)

You have a `task(...)` plugin. That is how you launch agents. Here is what it gives you:

| Plugin Call | What It Does |
|---|---|
| `task(subagent_type="...", run_in_background=true, ...)` | Launches a fresh agent. Returns a `task_id` and `session_id`. |
| `background_output(task_id="...")` | Reads the result after an agent finishes. Call this when the system notifies you. |
| `background_output(task_id="...", full_session=true, include_tool_results=true, message_limit=20)` | Inspects what a still-running agent is doing. Use if an agent looks stalled. |
| `background_cancel(taskId="...")` | Cancels ONE specific stuck agent. Never cancel all. |

**There is no timeout parameter.** Do not invent one.

**Always start fresh.** Do not continue old agent sessions in this workflow. Track `session_id`, but do not reuse it.

**There is no `background_cancel(all=true)`.** Never use it.

## Your Loop (Keep doing this until Step 1 shows 0 errors)

### STEP 1: Run Script

⚠️ **RE-READ THIS FILE FROM THE TOP BEFORE RUNNING THE SCRIPT.**

```bash
python3 .opencode/prepare_agent_dispatch.py
```

The script outputs agent names with error counts:
```
✗ Agent: workflow-reducer-cargo
  Dylint: 234 errors
  Compilation: 27 errors
  Clippy: 15 warnings
  Command: Read and execute: tmp/agent-instructions-workflow-reducer.txt

✓ Agent: workflow-config
  Dylint: 57 errors
  Compilation: 0 errors
  Clippy: 3 warnings
  Command: Read and execute: tmp/agent-instructions-workflow-config.txt

⚠️  TEST FAILURES DETECTED
  All agents: Read tmp/test-failures.txt and fix failures in YOUR modules only
```

**If all agents show 0 errors → YOU'RE DONE. STOP.**

**If any agent has errors → Continue to Step 2.**

**Important rule:** every time you run `python3 .opencode/prepare_agent_dispatch.py`, treat the regenerated instruction files as correct and idempotent. Do not rely on older instruction text.

### STEP 2: Dispatch ALL Agents With Errors IN PARALLEL

⚠️ **RE-READ THIS FILE FROM THE TOP BEFORE DISPATCHING.**

⚠️ **THESE INSTRUCTIONS MAY HAVE BEEN UPDATED. ALWAYS RE-READ.**

⚠️ **CRITICAL: DISPATCH ALL AGENTS IN PARALLEL. DO NOT DISPATCH ONE AT A TIME.**

⚠️ **IF THERE ARE 10 AGENTS WITH ERRORS → DISPATCH ALL 10 AT ONCE IN PARALLEL.**

Count how many agents have errors from Step 1 output. You MUST dispatch that many agents in parallel in a SINGLE message.

**Agent variant rules (from script output):**
- If agent has compilation errors → use `{agent}-cargo` variant (e.g., `workflow-reducer-cargo`)
- If agent has only dylint/clippy errors → use `{agent}` regular variant (e.g., `workflow-reducer`)

**Instruction file rule:**
- Every agent reads `tmp/agent-instructions-{agent}.txt`
- The instruction file name NEVER gets the `-cargo` suffix

**FOR EACH AGENT, USE THIS EXACT DISPATCH SHAPE:**

```
task(
  subagent_type="workflow-reducer-cargo",
  run_in_background=true,
  load_skills=[],
  description="Fix reducer module",
  prompt="1. TASK: Read and execute tmp/agent-instructions-workflow-reducer.txt.
2. EXPECTED OUTCOME: Fix every error assigned to workflow-reducer and report remaining blockers, if any.
3. REQUIRED TOOLS: Read, Grep, Glob, Edit/apply_patch, Bash, diagnostics.
4. MUST DO: Stay inside your module boundaries, run the verification command from the instruction file, report files changed and verification results. If the correct fix is complex, implement it completely — do not simplify away complexity. Do not stop after a partial fix if more assigned errors remain.
5. MUST NOT DO: Do not touch unrelated modules, do not commit, do not wait for further user input. Do not use #[allow(...)], do not leave TODOs or FIXMEs in changed files, do not use .unwrap()/.expect() outside boundary modules (io/, runtime/, ffi/, boundary/). Do not treat any error as pre-existing — every assigned error is your responsibility.
6. CONTEXT: This is one worker in a parallel cleanup batch; other modules are being handled by other agents simultaneously. The project style rules are strict: no lint suppression, no complexity avoidance, no dead code. Fix root causes, not symptoms."
)
```

**AFTER EACH DISPATCH, SAVE THE RETURNED `task_id` AND `session_id`.**

Keep a table like this in your head. Every agent is always in ONE of these states:

| State | Meaning |
|---|---|
| `running` | Agent was dispatched and has not reported back yet. |
| `done` | Agent completed all its work. Confirmed by you via `background_output`. |
| `stalled` | You inspected the agent and it is not making progress. Needs intervention. |
| `relaunched` | You cancelled it and dispatched a fresh one. Now treat the new one as `running`. |

Example table:

| Agent | task_id | session_id | Instruction File | State |
|---|---|---|---|---|
| workflow-reducer-cargo | task_abc | ses_123 | tmp/agent-instructions-workflow-reducer.txt | running |
| workflow-config | task_def | ses_456 | tmp/agent-instructions-workflow-config.txt | running |

Update the State column every time something happens. This is how you avoid duplicate dispatches and lost agents.

**PARALLEL DISPATCH EXAMPLE:**

If Step 1 shows 3 agents with errors, you MUST dispatch ALL 3 in ONE message using 3 `task(...)` calls:

```
task(subagent_type="workflow-reducer-cargo", run_in_background=true, load_skills=[], description="Fix reducer module", prompt="1. TASK: Read and execute tmp/agent-instructions-workflow-reducer.txt. ...")

task(subagent_type="workflow-json-cargo", run_in_background=true, load_skills=[], description="Fix json module", prompt="1. TASK: Read and execute tmp/agent-instructions-workflow-json.txt. ...")

task(subagent_type="workflow-config", run_in_background=true, load_skills=[], description="Fix config module", prompt="1. TASK: Read and execute tmp/agent-instructions-workflow-config.txt. ...")
```

**DO NOT dispatch them one at a time. ALL in ONE message.**

### STEP 3: End Your Turn and Wait For Events

⚠️ **DO NOT BLOCK. DO NOT POLL. DO NOT DO THE AGENTS' WORK YOURSELF.**

Once all agents are launched:
- If you have independent orchestration work that does NOT overlap with what the agents are doing → do it.
- Otherwise → **END YOUR RESPONSE IMMEDIATELY.**

The system will notify you when agents finish. You do NOT need to wait in-place.

You are event-driven now. That means:
- If ONE agent finishes, you may act before all others finish.
- If ONE agent is clearly stuck, you may act before all others finish.
- You do NOT need to wait for the whole batch before making the next orchestration decision.

**DO NOT:**
- Search for the same errors the agents are fixing
- Try to fix modules yourself while agents are running
- Call `background_output` in a loop hoping agents are done
- Sit and wait in your response

### STEP 4: On Any Agent Event, Process It Immediately

⚠️ **RE-READ THIS FILE FROM THE TOP BEFORE PROCESSING RESULTS.**

**Which `background_output` call to use:**

| Situation | Call |
|---|---|
| System sent a completion notification | `background_output(task_id="...")` — just read the final result. |
| Agent has been running for 10+ minutes with no completion notification | `background_output(task_id="...", full_session=true, include_tool_results=true, message_limit=20)` — inspect its live session. |

When the system sends you a completion notification for a task:

1. Call `background_output(task_id="<the task_id>")` to read the result.
2. Check: did the agent actually finish its assigned work?
3. Update the agent's State in your table:

| Outcome | What It Means | What To Do |
|---|---|---|
| `completed` | Agent fixed all assigned errors and ran verification | Go to STEP 4c. |
| `partial` | Agent fixed some errors but not all | Go to STEP 4a. |
| `blocked` | Agent hit a problem it cannot solve alone | Go to STEP 4a. |
| `failed` | Agent crashed or produced broken output | Go to STEP 4b. |

**IF AN AGENT LOOKS STALLED:**

Use this exact trigger:
- If an agent has NO completion notification after 10 minutes -> inspect it once.
- If an inspection shows no real progress -> it is stalled.

Inspect it:

```
background_output(task_id="...", full_session=true, include_tool_results=true, message_limit=20)
```

Then follow this exact rule:

1. **First inspection:** Read the session. Is the agent making real progress?
   - Real progress means at least one of these is true:
     - editing files
     - running new commands
     - producing new reasoning or output tied to the task
   - YES -> mark it `running`, wait another 10 minutes. Do not inspect again before then unless a completion event arrives.
   - NO -> mark it `stalled`. Go to STEP 4a to relaunch it as a fresh worker.
2. **Second inspection after a relaunch:**
   - If the relaunched agent again shows no real progress after another 10 minutes -> it is irrecoverable. Go to STEP 4b to cancel and relaunch.
   - If it shows progress -> mark it `running` and keep waiting.

Signs an agent is stalled:
- Repeating the same edit or command
- Producing no file changes
- Stuck reading the same file over and over
- Looping on a single error without making progress
- Saying it will do work but not actually doing work

**STEP 4a: Relaunch a partial/stuck agent as a fresh worker**

⚠️ **ALWAYS START FRESH. DO NOT REUSE `session_id`.**

⚠️ **FIRST RERUN THE SCRIPT.** The script regenerates idempotent instructions. Use the fresh instruction file, not memory.

Run:

```bash
python3 .opencode/prepare_agent_dispatch.py
```

```
task(
  subagent_type="workflow-reducer-cargo",
  run_in_background=true,
  load_skills=[],
  description="Retry reducer module",
  prompt="1. TASK: Read and execute tmp/agent-instructions-workflow-reducer.txt.
2. EXPECTED OUTCOME: Fix every remaining error assigned to workflow-reducer and report remaining blockers, if any.
3. REQUIRED TOOLS: Read, Grep, Glob, Edit/apply_patch, Bash, diagnostics.
4. MUST DO: Stay inside your module boundaries, rerun the verification command from the instruction file, report files changed and verification results, and treat this as a fresh attempt.
5. MUST NOT DO: Do not rely on prior session context, do not touch unrelated modules, do not commit, do not wait for further user input.
6. CONTEXT: A previous worker for this module was partial or stalled. The instruction file was regenerated by the script and is the source of truth. Start fresh from the regenerated instruction file and current repo state only."
)
```

Update the table: old row stays `stalled` or `partial`, new row becomes `running` with the new `task_id` and `session_id`.

**STEP 4b: Cancel and relaunch (ONLY if irrecoverable)**

```
background_cancel(taskId="<the stuck task_id>")
```

Then dispatch a fresh agent for that module only (same shape as Step 2). Update the table: old row becomes `relaunched`, new row becomes `running` with the new `task_id` and `session_id`.

⚠️ **NEVER use `background_cancel(all=true)`. Cancel ONE task at a time.**

**STEP 4c: Rerun the script early when an event changes the situation**

⚠️ **YOU DO NOT HAVE TO WAIT FOR ALL AGENTS BEFORE RERUNNING THE SCRIPT.**

Rerun the script immediately if ANY of these are true:
- an agent completed and may have cleared a module
- an agent reported partial progress and the failing set may have changed
- an agent was cancelled and relaunched
- you need fresh truth before dispatching more agents

Run:

```bash
python3 .opencode/prepare_agent_dispatch.py
```

Then use this simple rule:

- If the script shows NEW agents with errors that are not currently running -> dispatch them now
- If the script shows the SAME agents still need work, but they are already running -> do not duplicate them
- If the script shows a finished or stalled agent still has errors -> relaunch that one agent as a fresh worker
- If the script shows all current work is clean except for still-running agents -> let those running agents continue

This means the orchestrator can redispatch mid-batch. It is NOT forced to wait for every agent.

### STEP 5: Keep Reacting Until Step 1 Shows 0 Errors

⚠️ **STOP. RE-READ THIS ENTIRE FILE FROM THE TOP BEFORE CONTINUING.**

⚠️ **THE INSTRUCTIONS MAY HAVE BEEN UPDATED WHILE YOU WERE WORKING.**

⚠️ **DO NOT WORK FROM MEMORY. RE-READ EVERYTHING.**

Do NOT assume the repo is clean just because agents said they finished.

Instead, keep looping like this:
- agent event happens
- process it in STEP 4
- rerun the script if needed
- dispatch any newly-needed agents
- relaunch any partial/stuck agents as fresh workers
- repeat

Only stop when STEP 1 shows 0 errors for all agents.

**AFTER EACH LOOP: RE-READ THIS FILE FROM THE TOP.**

## Agent Names

- workflow-reducer
- workflow-json
- workflow-prompts
- workflow-config
- workflow-app
- workflow-misc
- workflow-future
- workflow-agents
- workflow-logging
- workflow-cloud
- workflow-monitoring
- workflow-core
- workflow-execution
- workflow-io
- workflow-workspace
- workflow-git

Use `-cargo` suffix if agent has compilation errors (e.g., `workflow-reducer-cargo`).

## Critical Checklist (DO THIS AT EVERY STEP)

Before EVERY action, ask yourself:

- [ ] Did I re-read this file from the top?
- [ ] Am I working from the current instructions or from memory?
- [ ] Have the instructions been updated since I last read them?
- [ ] Agent tool-call budget is **400 calls** — ensure dispatch prompts mention this.
- [ ] Am I about to dispatch ALL agents in PARALLEL (not one at a time)?
- [ ] Am I using the `task(...)` plugin to dispatch (not ad-hoc prose)?
- [ ] Did I set `run_in_background=true` on EVERY dispatch?
- [ ] Did I save the `task_id` and `session_id` for every agent?
- [ ] Am I using `background_output(task_id=...)` to collect results (not guessing)?
- [ ] If an agent has been quiet for 10+ minutes, did I inspect it once?
- [ ] Am I relaunching partial or stuck agents as fresh workers?
- [ ] Before relaunching, did I rerun the script so the regenerated instruction file is the source of truth?
- [ ] Did I rerun the script when an agent event changed the situation?
- [ ] Am I avoiding duplicate dispatch for agents that are already running?
- [ ] Did I count how many agents have errors and dispatch that exact number?
- [ ] Am I in Phase 1 (refactoring) — is all dylint work truly done before running the script?
- [ ] If in Phase 2 (compilation) — are agents fixing compilation errors WITHOUT introducing let mut/loops/interior mutability?
- [ ] Did I catch any agent running cargo mid-work and correct it?
- [ ] Am I dispatching fresh sessions (no session_id reuse)?

**IF YOU ANSWERED "NO" OR "NOT SURE" TO ANY → STOP AND RE-READ THIS FILE NOW.**

## Summary

Just loop:
1. **Re-read this file**
2. Run script → get list of N agents with errors
3. **Re-read this file**
4. Dispatch ALL N agents IN PARALLEL using `task(...)` plugin (single message, `run_in_background=true`)
5. Save every `task_id` and `session_id`
6. End your turn and wait for notifications
7. On any agent event -> `background_output(task_id=...)` -> check result
8. If an agent is quiet for 10+ minutes -> inspect once with `full_session=true`
9. Partial/stuck -> rerun the script, then relaunch as a fresh worker | wedged -> cancel that one task, rerun the script, and relaunch
10. If the event may have changed the failing set -> rerun the script immediately
11. Dispatch any newly-needed agents that are not already running
12. **Re-read this file** -> keep looping until step 1 shows 0 errors

**CRITICAL RULES:**
- ✅ ALWAYS DISPATCH IN PARALLEL (10 agents = 10 parallel `task(...)` calls in ONE message)
- ✅ ALWAYS USE `run_in_background=true`
- ✅ ALWAYS SAVE `task_id` AND `session_id`
- ✅ ALWAYS COLLECT RESULTS WITH `background_output(task_id=...)`
- ✅ ALWAYS INSPECT AGENTS THAT HAVE BEEN QUIET FOR 10+ MINUTES
- ✅ ALWAYS RELAUNCH PARTIAL OR STUCK AGENTS AS FRESH WORKERS
- ✅ ALWAYS RERUN THE SCRIPT BEFORE ANY RELAUNCH SO THE REGENERATED INSTRUCTIONS ARE THE SOURCE OF TRUTH
- ✅ ALWAYS RERUN THE SCRIPT WHEN AN AGENT EVENT MAY HAVE CHANGED THE FAILING SET
- ✅ ALWAYS AVOID DUPLICATE DISPATCH FOR AGENTS THAT ARE ALREADY RUNNING
- ✅ ALWAYS RE-READ THIS FILE AT EACH STEP
- ✅ NEVER USE `background_cancel(all=true)`
- ✅ NEVER DO THE AGENTS' WORK YOURSELF WHILE THEY ARE RUNNING
- ✅ NEVER TRUST AGENT SELF-REPORTS — RERUN THE SCRIPT TO VERIFY
- ✅ INSTRUCTIONS MAY UPDATE — NEVER WORK FROM MEMORY

---

## 🔮 POST-REFACTOR PHASE 2: State Monad Architecture

After dylint violations reach zero and all compilation passes, the following architectural improvements must be done as a second pass. These are NOT optional style preferences — they are the correct functional architecture.

### The Problem

Many `io.rs` and `runtime.rs` boundary files were created to exempt RefCell/mutable state from dylint lints. But most of this state is NOT actually I/O — it's computation state that should be threaded functionally. True boundaries should only wrap real I/O: syscalls, file handles, sockets, process spawning, stdin/stdout.

### The Pattern (State Monad in Rust)

```rust
// WRONG — mutable state hidden behind RefCell in "boundary":
struct ParserState {
    session: RefCell<StreamingSession>,  // not I/O!
    printer: RefCell<Printer>,           // not I/O!
}

// CORRECT — pure state threaded through computation:
struct ParserState {
    session: StreamingSession,   // pure value
    printer: Printer,            // pure value
}

impl Printer {
    fn write_text(self, text: &str) -> Self { ... }  // monadic step
    fn write_line(self, line: &str) -> Self { ... }
    fn emit(self) -> (Self, String) { ... }          // extract output
}
```

### Candidates for Monad Conversion (post-dylint-zero)

| Type | Current (Wrong) | Should Be |
|---|---|---|
| `StreamingSession` | `RefCell<StreamingSession>` in io.rs | Pure value threaded through parse calls |
| `Printer` / `VirtualTerminalState` | `RefCell<Printer>` in io.rs | `write_text(self) -> Self` pure value |
| `IncrementalNdjsonParser` | `&mut self` feed method | `feed(self, byte) -> (Self, Vec<Event>)` |
| `DeltaAccumulator` | mutable accumulation | Pure fold |
| `BoundedEventQueue` | `&mut self push` | `push(self, event) -> Self` |
| `CommitLogSession` | mutable log state | Pure log value threaded through phases |
| `AgentRegistry` | `apply_unified_config(&mut self)` | `with_config(self, config) -> Self` |
| `AnsiParser` | `while let Some(c) = chars.next()` | `parse(self, input: &str) -> (Self, AnsiOutput)` |

### True Boundary (IO Monad Equivalent)

Only these belong in `io.rs` / `runtime.rs`:
- Reading bytes from process stdout/stderr
- Writing rendered output to stdout
- File system reads/writes
- Process spawning and lifecycle
- Thread creation and synchronization primitives

### Audit Steps

1. For each `io.rs` and `runtime.rs` file: verify every field/function is ACTUAL I/O, not just computation hidden there to avoid lints
2. Move non-I/O state OUT of boundary files into domain types with monadic APIs
3. Remove RefCell from domain state entirely — it should only exist where truly needed for shared mutable state across thread boundaries
4. Verify: if a type could be Haskell's `State s a`, it should be a pure state-threading function in Rust
