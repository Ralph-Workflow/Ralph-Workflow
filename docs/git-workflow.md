# Git Workflow Architecture

This document describes the current Git workflow used by the Python implementation in `ralph-workflow/`.

## Overview

Ralph keeps agents focused on code changes while the orchestrator owns repository state, staging, commits, hooks, and optional rebases.

Current implementation modules:

- `ralph-workflow/ralph/git/operations.py` — repository discovery, staging, commit creation, push helpers
- `ralph-workflow/ralph/git/hooks.py` — managed hook installation and tamper detection
- `ralph-workflow/ralph/git/wrapper.py` — guardrails around agent phases and unauthorized commits
- `ralph-workflow/ralph/git/rebase/` — rebase helpers, checkpointing, and continuation logic

## Design rules

- Agents should not be responsible for direct git operations.
- The orchestrator decides when to stage, commit, or continue/recover a rebase.
- Repository behavior should stay deterministic enough for unattended runs and resume flows.
- Public docs should describe GitPython-backed behavior, not the retired Rust/libgit2 implementation.

## Verification touchpoints

When changing Git behavior, verify from `ralph-workflow/` with:

```bash
pytest tests/test_git_operations.py -v
pytest tests/test_git_hooks.py -v
pytest tests/test_git_rebase.py -v
pytest tests/test_git_rebase_preconditions.py -v
pytest tests/test_git_rebase_continuation.py -v
pytest tests/test_git_rebase_checkpoint.py -v
```

Then run the full package verification:

```bash
make verify
```