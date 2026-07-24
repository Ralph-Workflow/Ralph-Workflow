---
type: plan
schema_version: 1
---

## Summary

Reject empty or whitespace-only project names before file creation while preserving valid-name behavior.

## Steps

### [S-1] Add failing invalid-name coverage

Cover empty and whitespace-only project names before changing the create flow.

Type: test
Files:
- modify tests/test_create.py

### [S-2] Validate normalized names before file creation

Normalize the supplied name and return a clear error before any project files are created when the result is empty.

Type: file_change
Files:
- modify cli/create.py
Depends on: S-1

### [S-3] Verify the focused behavior

Run the focused create-flow tests.

Type: verify
Depends on: S-2
Verify: pytest tests/test_create.py
Expect: empty and whitespace-only names fail, valid names pass, and no invalid-name files are created

## Acceptance Criteria

- [AC-01] Empty and whitespace-only names fail with a clear error
  Satisfied by: S-1, S-2
  Verify: pytest tests/test_create.py
  Expect: invalid-name cases pass without creating project files
- [AC-02] Existing valid-name behavior remains unchanged
  Satisfied by: S-1, S-2
  Verify: pytest tests/test_create.py
  Expect: valid-name cases pass

## Verification

- [V-1] pytest tests/test_create.py
  Expect: the focused suite passes with no failures
