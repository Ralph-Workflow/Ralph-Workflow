# Python Orchestration Parity Completion Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete remaining parity gaps in ralph-python against `docs/plans/2026-04-09-configurable-orchestration.md` by wiring MCP/session startup in runtime invocation, adding explicit commit post-routing for `development_analysis`/`review_analysis` topology, and adding planning `work_units` parsing/validation hooks.

**Architecture:** Keep existing reducer/orchestrator/event-loop shape, add policy-driven post-commit routing and lightweight work-unit contracts, and inject MCP bridge lifecycle around each agent invocation in the runner with deterministic cleanup.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest, ruff, mypy.

---

### Task 1: Add policy model support for post-commit routes

**Files:**
- Modify: `ralph-python/ralph/policy/models.py`
- Modify: `ralph-python/ralph/policy/defaults/pipeline.toml`
- Test: `ralph-python/tests/test_orchestrator.py`

### Task 2: Implement post-commit route evaluation in orchestrator/reducer

**Files:**
- Modify: `ralph-python/ralph/pipeline/orchestrator.py`
- Modify: `ralph-python/ralph/pipeline/reducer.py`
- Test: `ralph-python/tests/test_orchestrator.py`
- Test: `ralph-python/tests/test_reducer.py`

### Task 3: Add session drain parity and MCP startup lifecycle in runner

**Files:**
- Modify: `ralph-python/ralph/mcp/capability_mapping.py`
- Modify: `ralph-python/ralph/pipeline/runner.py`
- Modify: `ralph-python/ralph/agents/invoke.py`
- Test: `ralph-python/tests/test_mcp_startup.py`
- Test: `ralph-python/tests/test_pipeline_runner.py`

### Task 4: Add planning work_units schema + parser/validator hook

**Files:**
- Create: `ralph-python/ralph/pipeline/work_units.py`
- Modify: `ralph-python/ralph/policy/models.py`
- Modify: `ralph-python/ralph/policy/validation.py`
- Test: `ralph-python/tests/test_work_units.py`

### Task 5: Verify full suite

**Files:**
- Run: `cd ralph-python && make verify`
