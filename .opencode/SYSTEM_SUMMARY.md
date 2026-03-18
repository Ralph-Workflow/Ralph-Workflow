# Agent Dispatch System Summary

## Overview

DEAD SIMPLE deterministic system for orchestrating code cleanup agents.

## Components

### 1. Preparation Script (`prepare_agent_dispatch.py`)
**Does EVERYTHING automatically:**
- Runs `cargo xtask dylint-report`
- Runs `cargo build` (compiles app code)
- **IF build succeeds** → Runs `cargo test` (compiles test code + runs tests)
- **IF build succeeds** → Runs `cargo clippy` (catches warnings)
- Parses all errors and filters by agent
- Generates instruction files for each agent
- Prints dispatch summary with correct agent variants

**Output:**
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

### 2. Orchestrator Instructions (`orch-instruct.md`)
**DEAD SIMPLE loop:**
1. Run `python3 .opencode/prepare_agent_dispatch.py`
2. Dispatch agents with 20-minute timeout
3. Wait for reports
4. Loop back to step 1

**Key rules:**
- ✅ RE-READ FILE BEFORE EVERY ACTION
- ✅ 20 MINUTE TIMEOUT ON EVERY DISPATCH
- ✅ Use `-cargo` variant if compilation errors exist
- ✅ Use regular variant if only dylint errors
- ✅ Loop until all agents show 0 errors

### 3. Agent Instructions (generated files)
**Good detail for agents:**
- Re-read before every fix reminder
- Goal: Fix ALL errors
- Step-by-step instructions with refactoring examples
- Error file paths (dylint + compilation)
- Verification command

### 4. Verification Script (`verify_agent_work.sh`)
**Agents run this when done:**
```bash
.opencode/verify_agent_work.sh
```

Runs clippy to verify style guide compliance.

## Workflow

```
┌─────────────────────────────────────────────┐
│ 1. Orchestrator runs preparation script    │
│    python3 .opencode/prepare_agent_dispatch.py │
└────────────────┬────────────────────────────┘
                 │
                 v
┌─────────────────────────────────────────────┐
│ 2. Script outputs agent dispatch list      │
│    ✗ workflow-reducer-cargo: 234/27 errors │
│    ✓ workflow-config: 57/0 errors          │
└────────────────┬────────────────────────────┘
                 │
                 v
┌─────────────────────────────────────────────┐
│ 3. Orchestrator dispatches each agent      │
│    - 20 minute timeout                      │
│    - Correct variant (-cargo or regular)   │
│    - Instruction file path                  │
└────────────────┬────────────────────────────┘
                 │
                 v
┌─────────────────────────────────────────────┐
│ 4. Agent executes instructions              │
│    - Reads style guide                      │
│    - Reads error files                      │
│    - Fixes code                             │
│    - Runs verification                      │
│    - Reports results                        │
└────────────────┬────────────────────────────┘
                 │
                 v
┌─────────────────────────────────────────────┐
│ 5. Loop back to step 1                      │
│    - Re-run script to get updated counts    │
│    - Continue until all agents = 0 errors   │
└─────────────────────────────────────────────┘
```

## Files Generated

```
tmp/
├── agent-instructions-workflow-*.txt    # 10 instruction files
├── compilation-errors/
│   ├── workflow-*.txt                   # Per-agent filtered errors
│   └── summary.txt                      # Total error count
├── clippy-warnings/
│   └── workflow-*.txt                   # Per-agent filtered warnings
├── test-failures.txt                    # ALL test failures (agents check if in their modules)
├── build-current.txt                    # Full cargo build output
├── test-output.txt                      # Full cargo test output (only if build succeeds)
├── clippy-output.txt                    # Full clippy output (only if build succeeds)
└── dylint-*.txt                         # 23 module dylint reports
```

## Agent Variant Logic

| Condition | Agent Variant | Instruction File |
|-----------|---------------|------------------|
| Has compilation errors | `{agent}-cargo` | `tmp/agent-instructions-{agent}.txt` |
| Only dylint errors | `{agent}` | `tmp/agent-instructions-{agent}.txt` |

**Example:**
- `workflow-reducer-cargo` → reads `tmp/agent-instructions-workflow-reducer.txt`
- `workflow-config` → reads `tmp/agent-instructions-workflow-config.txt`

## Success Criteria

Script shows:
```
✓ workflow-reducer: 0 dylint, 0 compilation errors, 0 clippy warnings
✓ workflow-json: 0 dylint, 0 compilation errors, 0 clippy warnings
✓ workflow-config: 0 dylint, 0 compilation errors, 0 clippy warnings
...

No test failures
```

**Then you're done!**

## Key Improvements

1. **Conditional execution**: Tests and clippy only run if build succeeds
2. **Clippy warnings**: Included and filtered by agent (same logic as compilation errors)
3. **Test failures**: All failures in one file, agents check if in their modules
4. **Agent boundaries**: Agents are warned NOT to fix tests outside their modules
