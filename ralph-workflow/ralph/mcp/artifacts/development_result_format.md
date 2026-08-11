# Development result visual evidence format

A completed UI proof remains a normal `## Plan Items Proven` list item. Add
these labeled body fields beneath the item when it cites visual evidence:

```markdown
- [S-4] The header UI is capture-backed.
  Verdict ID: verdict-001
  Before Captures: ralph://media/{before-artifact-id}
  After Captures: ralph://media/{after-artifact-id}
```

`Verdict ID` identifies the submitted `design_verdict`. `Before Captures` and
`After Captures` are comma-separated `ralph://media/{artifact_id}` handles for
the compared capture sets. A UI proof is valid only when the verdict and every
handle are minted for the active run's authenticated capture ledger. CSS,
class, style, and DOM assertions remain implementation evidence rather than
design evidence.
