# Scripts

This directory contains utility scripts for development and testing.

## Test Auditing

### audit_tests.sh

Audits integration tests for common anti-patterns that violate the integration testing guide.

**Usage:**
```bash
bash scripts/audit_tests.sh
```

**Checks:**
- `cfg!(test)` usage in production code
- Real filesystem usage (`std::fs`, `TempDir`) in integration tests
- Real process execution in tests
- Proper use of `MemoryWorkspace` and `MockProcessExecutor`
- Test files exceeding 1000 lines
- Internal field assertions

**Expected output:** No ERROR/WARNING diagnostics for compliant tests. Any violations will be reported with file locations.

### check-tests.sh

Pre-commit check for integration test anti-patterns in staged files.

**Usage:**
```bash
# Check staged test files before committing
bash scripts/check-tests.sh

# Run as part of CI
bash scripts/check-tests.sh
```

**Checks (on staged files only):**
- ❌ No `std::fs` usage in integration tests (use MemoryWorkspace)
- ❌ No `TempDir` usage (use MemoryWorkspace)
- ❌ No `std::process::Command` (use MockProcessExecutor)
- ❌ No `cfg!(test)` in production code (use dependency injection)
- ⚠️  Warns if files exceed 1000 lines

**Exit codes:**
- `0` - All checks passed
- `1` - Anti-patterns detected

**Note:** Only checks staged files, won't slow down your workflow.

## Git Hooks

### pre-commit-hook

Ralph-managed pre-commit hook. The repository uses `scripts/check-tests.sh` for test validation.

**Manual check before commit:**
```bash
bash scripts/check-tests.sh && git commit
```

## Related Documentation

- [docs/agents/testing-guide.md](../docs/agents/testing-guide.md) - Canonical test strategy, rules, and patterns
- [docs/agents/verification.md](../docs/agents/verification.md) - Required pre-PR verification commands
