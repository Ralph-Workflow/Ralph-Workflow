# commit_cleanup artifact format

## What you are doing

You are analyzing the current git state to identify files that MUST NOT be committed, such as binaries, build artifacts, editor temporary files, and machine-local configuration. You recommend actions to clean up the commit.

## How to submit

Call the `ralph_submit_artifact` tool with `artifact_type` set to `"commit_cleanup"` and `content` set to a JSON string of your cleanup payload.

```json
{
  "artifact_type": "commit_cleanup",
  "content": "{\"analysis_complete\": false, \"actions\": [{\"action\": \"add_to_gitignore\", \"pattern\": \"*.exe\"}]}"
}
```

## Required fields (inside content)

- `analysis_complete` — boolean, set to `true` when there is nothing more to clean up or all cleanup is done
- `actions` — array of action objects (can be empty if analysis_complete is true)

Each action object must have:
- `action` — one of `"delete_file"`, `"add_to_gitignore"`, `"add_to_git_exclude"`
- `path` — required for `"delete_file"` action (the file to remove from the repo)
- `pattern` — required for `"add_to_gitignore"` and `"add_to_git_exclude"` actions

## Optional fields

- `reason` — string, an optional explanation for the cleanup decision

## Complete example — must contain a ```json block

```json
{
  "artifact_type": "commit_cleanup",
  "content": "{\"analysis_complete\": false, \"actions\": [{\"action\": \"delete_file\", \"path\": \"build/output.exe\"}, {\"action\": \"add_to_gitignore\", \"pattern\": \"*.pyc\"}, {\"action\": \"add_to_git_exclude\", \"pattern\": \".env.local\"}]}"
}
```

## Common mistakes

- Do NOT recommend deleting source files, test files, or documentation
- Do NOT recommend deleting configuration files that were intentionally modified
- Do NOT change the semantic meaning of the commit — only clean up obvious build artifacts
- Do NOT use `add_to_gitignore` for machine-local patterns; use `add_to_git_exclude` instead
- Do NOT delete files that are part of the actual commit (source code, tests, docs)

## Dumb-proof checklist

- Did you set `artifact_type` to `"commit_cleanup"`?
- Did you set `analysis_complete` to `true` when there is nothing more to clean?
- Did you use `delete_file` only for actual binary/generated files present in the diff?
- Did you use `add_to_gitignore` for project-wide patterns like `*.pyc`?
- Did you use `add_to_git_exclude` for machine-local files like `.env.local`?
- Did you NOT recommend deleting source code, test files, or documentation?
