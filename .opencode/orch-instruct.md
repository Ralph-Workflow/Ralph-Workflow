# ORCHESTRATOR INSTRUCTIONS

⚠️ **RE-READ THIS FILE BEFORE EVERY ACTION. DO NOT WORK FROM MEMORY.**

⚠️ **EVERY AGENT DISPATCH MUST HAVE 20 MINUTE TIMEOUT. NO EXCEPTIONS.**

## Your Loop (Keep doing this until Step 1 shows 0 errors)

### STEP 1: Run Script

⚠️ **RE-READ THIS FILE FROM THE TOP BEFORE RUNNING THE SCRIPT.**

```bash
python3 .opencode/prepare_agent_dispatch.py
```

The script outputs agent names with error counts:
```
✓ workflow-reducer: 234 dylint, 27 compilation errors
✗ workflow-json: 162 dylint, 105 compilation errors
✓ workflow-config: 57 dylint, 0 compilation errors
```

**If all agents show 0 errors → YOU'RE DONE. STOP.**

**If any agent has errors → Continue to Step 2.**

### STEP 2: Dispatch ALL Agents With Errors IN PARALLEL

⚠️ **RE-READ THIS FILE FROM THE TOP BEFORE DISPATCHING.**

⚠️ **THESE INSTRUCTIONS MAY HAVE BEEN UPDATED. ALWAYS RE-READ.**

⚠️ **CRITICAL: DISPATCH ALL AGENTS IN PARALLEL. DO NOT DISPATCH ONE AT A TIME.**

⚠️ **IF THERE ARE 10 AGENTS WITH ERRORS → DISPATCH ALL 10 AT ONCE IN PARALLEL.**

Count how many agents have errors from Step 1 output. You MUST dispatch that many agents in parallel in a SINGLE message.

⚠️ **CRITICAL: SET 20 MINUTE TIMEOUT ON EVERY DISPATCH. NO EXCEPTIONS.**

For EACH agent with errors, send this EXACT prompt with 20-minute timeout:
```
Read and execute: tmp/agent-instructions-{agent}.txt
```

**Agent variant rules (from script output):**
- If agent has compilation errors → use `{agent}-cargo` variant (e.g., `workflow-reducer-cargo`)
- If agent has only dylint errors → use `{agent}` regular variant (e.g., `workflow-reducer`)

**PARALLEL DISPATCH EXAMPLE:**

If Step 1 shows these 3 agents with errors:
- workflow-reducer-cargo (234 dylint, 27 compilation)
- workflow-json-cargo (162 dylint, 105 compilation)  
- workflow-config (57 dylint, 0 compilation)

You MUST dispatch ALL 3 in parallel in ONE message:

```
Dispatching 3 agents in parallel with 20-minute timeout each:

Agent 1: workflow-reducer-cargo
Timeout: 20 minutes
Prompt: Read and execute: tmp/agent-instructions-workflow-reducer.txt

Agent 2: workflow-json-cargo  
Timeout: 20 minutes
Prompt: Read and execute: tmp/agent-instructions-workflow-json.txt

Agent 3: workflow-config
Timeout: 20 minutes
Prompt: Read and execute: tmp/agent-instructions-workflow-config.txt
```

**DO NOT dispatch them one at a time. Dispatch ALL in parallel in a SINGLE message.**

### STEP 3: Wait For ALL Agent Reports

⚠️ **WHILE WAITING, RE-READ THIS FILE TO PREPARE FOR NEXT STEP.**

Wait for ALL agents to report. Each agent will report when done:
```
FILES CHANGED: [list]
DYLINT FIXED: X/Y
CLIPPY: pass/fail
```

If you dispatched 10 agents, wait for all 10 reports before continuing.

### STEP 4: Loop Back (No "Next Agent")

⚠️ **THERE IS NO "NEXT AGENT" STEP.**

⚠️ **YOU ALREADY DISPATCHED ALL AGENTS IN PARALLEL IN STEP 2.**

⚠️ **GO DIRECTLY TO STEP 5.**

### STEP 5: When ALL Agents Report Back

⚠️ **STOP. RE-READ THIS ENTIRE FILE FROM THE TOP BEFORE CONTINUING.**

⚠️ **THE INSTRUCTIONS MAY HAVE BEEN UPDATED WHILE YOU WERE WORKING.**

⚠️ **DO NOT WORK FROM MEMORY. RE-READ EVERYTHING.**

Once ALL agents have reported (if you dispatched 10, wait for all 10):

**GO BACK TO STEP 1.** Re-run the script. It will show updated error counts.

**RE-READ THIS FILE FROM THE TOP AGAIN.**

Loop until Step 1 shows 0 errors for all agents.

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

Use `-cargo` suffix if agent needs cargo permissions (e.g., `workflow-reducer-cargo`).

## Critical Checklist (DO THIS AT EVERY STEP)

Before EVERY action, ask yourself:

- [ ] Did I re-read this file from the top?
- [ ] Am I working from the current instructions or from memory?
- [ ] Have the instructions been updated since I last read them?
- [ ] Am I about to dispatch ALL agents in PARALLEL (not one at a time)?
- [ ] Am I setting a 20-minute timeout on EVERY dispatch?
- [ ] Did I count how many agents have errors and dispatch that exact number?

**IF YOU ANSWERED "NO" OR "NOT SURE" TO ANY → STOP AND RE-READ THIS FILE NOW.**

## Summary

Just loop: 
1. **Re-read this file**
2. Run script → get list of N agents with errors
3. **Re-read this file** 
4. Dispatch ALL N agents IN PARALLEL (single message, 20-min timeout each)
5. Wait for ALL N reports
6. **Re-read this file**
7. Loop back to step 1

**CRITICAL RULES:**
- ✅ ALWAYS DISPATCH IN PARALLEL (10 agents = 10 parallel dispatches in ONE message)
- ✅ ALWAYS SET 20-MINUTE TIMEOUT ON EACH
- ✅ ALWAYS RE-READ THIS FILE AT EACH STEP
- ✅ INSTRUCTIONS MAY UPDATE - NEVER WORK FROM MEMORY
