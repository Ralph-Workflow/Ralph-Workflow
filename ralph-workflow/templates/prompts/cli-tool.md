# CLI Tool: [Name of the command/tool]

## Purpose
<!-- What problem does this solve? Who uses it and when? -->
[e.g., "Developers use this to sync local files with remote storage during deployment"]

## Usage
<!-- How should users invoke it? Show the main commands -->
```
[e.g., "sync upload <path> --bucket <name>"]
[e.g., "sync download <path> --filter '*.json'"]
[e.g., "sync status"]
```

## Workflow
<!-- When does someone use this? What's the typical flow? -->
1. [e.g., "Developer makes local changes"]
2. [e.g., "Runs 'sync upload' to push to staging"]
3. [e.g., "Verifies with 'sync status'"]

## Inputs & Outputs
- **Inputs:** [e.g., "File paths, bucket name, optional filters"]
- **Outputs:** [e.g., "Progress bar during transfer, summary when done"]

## Error Handling
<!-- What can go wrong? How should the CLI respond? -->
- [e.g., "Network failure → retry with exponential backoff, show progress"]
- [e.g., "File not found → clear error message with path"]

## Context (optional)
<!-- Existing CLI patterns, config file locations -->
[e.g., "Should follow existing CLI conventions in the project"]
