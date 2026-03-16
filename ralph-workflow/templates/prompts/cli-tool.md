# CLI Tool: [Name of the command/tool]

## Goal
<!-- What problem does this solve? Who uses it and when? -->
[e.g., "Developers can sync local files with remote storage in one command during deployment"]

## Usage
<!-- How should users invoke it? Show the main commands -->
```
[e.g., "sync upload <path> --bucket <name>"]
[e.g., "sync download <path> --filter '*.json'"]
[e.g., "sync status"]
```

## User Workflow
<!-- When does someone use this? What's the typical flow? -->
1. [e.g., "Developer makes local changes"]
2. [e.g., "Runs 'sync upload' to push to staging"]
3. [e.g., "Verifies with 'sync status'"]

## Output
<!-- What should the user see when the command runs? -->
[e.g., "Progress bar during transfer, summary showing files uploaded and any errors"]

## Error Experience
<!-- What should users see when things go wrong? -->
- [e.g., "Network failure → clear message with retry suggestion"]
- [e.g., "File not found → specific error showing which path failed"]

## Acceptance
<!-- Observable conditions that prove this CLI tool is complete -->
- [ ] [e.g., "All documented commands work as shown in Usage section"]
- [ ] [e.g., "Help text accurately describes all options"]
- [ ] [e.g., "Error messages are actionable and user-friendly"]
