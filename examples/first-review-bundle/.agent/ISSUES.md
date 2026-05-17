# Review Issues

## Issue 1

The first pass validated empty strings but missed whitespace-only names like `"   "`.

### Why it matters

Whitespace-only input would still create files even though it should be rejected.

### Fix requested

Trim or normalize the input before the validation check, then re-run the focused tests.
