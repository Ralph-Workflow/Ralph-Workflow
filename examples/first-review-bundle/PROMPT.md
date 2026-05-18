# Goal

Add validation so the CLI rejects empty or whitespace-only project names before creating files.
Keep the rest of the create flow unchanged.

## Acceptance criteria

- Empty or whitespace-only project names fail with a clear error
- No project files are created for invalid names
- Existing valid-name behavior stays unchanged
- Tests cover the new validation
