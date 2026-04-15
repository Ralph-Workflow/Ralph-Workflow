# Template Customization Guide

This guide explains how to customize the prompt templates used by the maintained Python package.

## Current template locations

Packaged templates live under:

```text
ralph-python/ralph/prompts/templates/
```

Relevant implementation modules:

- `ralph-python/ralph/prompts/template_registry.py`
- `ralph-python/ralph/prompts/template_engine.py`
- `ralph-python/ralph/prompts/template_parsing.py`
- `ralph-python/ralph/prompts/materialize.py`

Workspace overrides are discovered from:

- `.agent/prompts/shared/`
- `.agent/prompts/`
- `.agent/prompts/partials/`

## Template format

The current package uses Jinja-based templates (`.jinja`, `.j2`, and `.txt` partials).

Examples in the packaged template tree include:

- `planning.jinja`
- `developer_iteration.jinja`
- `review.jinja`
- `fix_mode.jinja`
- `commit_message.jinja`
- `shared/_context_section.jinja`
- `shared/_mcp_tools.jinja`

## Safe customization workflow

1. Edit or add a workspace template override in `.agent/prompts/`.
2. Keep variable names aligned with the Python prompt materialization code.
3. Validate the package from `ralph-python/`:

```bash
pytest tests/test_prompts.py -v
pytest tests/test_prompt_template_files.py -v
pytest tests/test_prompt_materialize.py -v
make verify
```

## Legacy note

Older documentation in this repository may refer to Rust source paths such as `ralph-workflow/src/prompts/templates/` or XML/XSD-specific prompt plumbing. Those references are historical and do not describe the maintained Python package.