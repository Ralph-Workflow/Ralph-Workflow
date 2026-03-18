# ORCHESTRATOR INSTRUCTIONS

⚠️ **RE-READ THIS FILE BEFORE EVERY ACTION. DO NOT WORK FROM MEMORY.**

⚠️ **YOU DISPATCH AGENTS WITH THE `task` PLUGIN. NOT AD-HOC PROSE.**

⚠️ **ALL AGENTS RUN IN BACKGROUND: `run_in_background=true`. NO EXCEPTIONS.**

## How You Dispatch (Read This First)

You have a `task(...)` plugin. That is how you launch agents. Here is what it gives you:

| Plugin Call | What It Does |
|---|---|
| `task(subagent_type="...", run_in_background=true, ...)` | Launches an agent. Returns a `task_id` and `session_id`. |
| `background_output(task_id="...")` | Reads the result after an agent finishes. Call this when the system notifies you. |
| `background_output(task_id="...", full_session=true, include_tool_results=true, message_limit=20)` | Inspects what a still-running agent is doing. Use if an agent looks stalled. |
| `background_cancel(taskId="...")` | Cancels ONE specific stuck agent. Never cancel all. |
| `task(session_id="...", run_in_background=true, ...)` | Continues a previous agent session. The agent keeps all its context. |

**There is no timeout parameter.** Do not invent one.

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
4. MUST DO: Stay inside your module boundaries, run the verification command from the instruction file, report files changed and verification results.
5. MUST NOT DO: Do not touch unrelated modules, do not commit, do not wait for further user input, do not stop after a partial fix if more assigned errors remain.
6. CONTEXT: This is one worker in a parallel cleanup batch; other modules are being handled by other agents simultaneously."
)
```

**AFTER EACH DISPATCH, SAVE THE RETURNED `task_id` AND `session_id`.**

Keep a table like this in your head:

| Agent | task_id | session_id | Instruction File | Status |
|---|---|---|---|---|
| workflow-reducer-cargo | task_abc | ses_123 | tmp/agent-instructions-workflow-reducer.txt | running |
| workflow-config | task_def | ses_456 | tmp/agent-instructions-workflow-config.txt | running |

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

When the system sends you a completion notification for a task:

1. Call `background_output(task_id="<the task_id>")` to read the result.
2. Check: did the agent actually finish its assigned work?
3. Record the outcome in your table:

| Outcome | What It Means | What To Do |
|---|---|---|
| `completed` | Agent fixed all assigned errors and ran verification | Go to STEP 4c. |
| `partial` | Agent fixed some errors but not all | Go to STEP 4a. |
| `blocked` | Agent hit a problem it cannot solve alone | Go to STEP 4a. |
| `failed` | Agent crashed or produced broken output | Go to STEP 4b. |

**IF AN AGENT LOOKS STALLED (no notification for a long time, or the session shows no real progress):**

Call `background_output(task_id="...", full_session=true, include_tool_results=true, message_limit=20)` to inspect what it is doing.

- If it is making progress -> wait longer.
- If it is repeating itself, doing no real work, or stuck in a loop -> go to STEP 4a.
- If it is completely wedged -> go to STEP 4b.

**STEP 4a: Continue a partial/stuck agent (USE session_id)**

⚠️ **DO NOT START A FRESH AGENT. CONTINUE THE SAME SESSION.**

```
task(
  session_id="<the agent's session_id from your table>",
  run_in_background=true,
  load_skills=[],
  description="Continue reducer fix",
  prompt="Fix the remaining issues from tmp/agent-instructions-workflow-reducer.txt. Verification failed because <reason>. Finish the assigned module only and rerun verification before reporting back."
)
```

The agent keeps all its context. It knows what it already tried. This is faster and smarter than starting over.

**STEP 4b: Cancel and relaunch (ONLY if irrecoverable)**

```
background_cancel(taskId="<the stuck task_id>")
```

Then dispatch a fresh agent for that module only (same shape as Step 2).

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
- If the script shows a finished agent still has errors -> continue that agent with its `session_id` or relaunch that one agent
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
- continue any partial/stuck agents
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
- [ ] Am I about to dispatch ALL agents in PARALLEL (not one at a time)?
- [ ] Am I using the `task(...)` plugin to dispatch (not ad-hoc prose)?
- [ ] Did I set `run_in_background=true` on EVERY dispatch?
- [ ] Did I save the `task_id` and `session_id` for every agent?
- [ ] Am I using `background_output(task_id=...)` to collect results (not guessing)?
- [ ] Am I continuing stuck agents with `session_id` (not starting fresh)?
- [ ] Did I rerun the script when an agent event changed the situation?
- [ ] Am I avoiding duplicate dispatch for agents that are already running?
- [ ] Did I count how many agents have errors and dispatch that exact number?

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
8. Partial/stuck -> continue with `session_id` | Wedged -> cancel that one task and relaunch
9. If the event may have changed the failing set -> rerun the script immediately
10. Dispatch any newly-needed agents that are not already running
11. **Re-read this file** -> keep looping until step 1 shows 0 errors

**CRITICAL RULES:**
- ✅ ALWAYS DISPATCH IN PARALLEL (10 agents = 10 parallel `task(...)` calls in ONE message)
- ✅ ALWAYS USE `run_in_background=true`
- ✅ ALWAYS SAVE `task_id` AND `session_id`
- ✅ ALWAYS COLLECT RESULTS WITH `background_output(task_id=...)`
- ✅ ALWAYS CONTINUE PARTIAL AGENTS WITH `session_id` (not fresh dispatch)
- ✅ ALWAYS RERUN THE SCRIPT WHEN AN AGENT EVENT MAY HAVE CHANGED THE FAILING SET
- ✅ ALWAYS AVOID DUPLICATE DISPATCH FOR AGENTS THAT ARE ALREADY RUNNING
- ✅ ALWAYS RE-READ THIS FILE AT EACH STEP
- ✅ NEVER USE `background_cancel(all=true)`
- ✅ NEVER DO THE AGENTS' WORK YOURSELF WHILE THEY ARE RUNNING
- ✅ NEVER TRUST AGENT SELF-REPORTS — RERUN THE SCRIPT TO VERIFY
- ✅ INSTRUCTIONS MAY UPDATE — NEVER WORK FROM MEMORY
