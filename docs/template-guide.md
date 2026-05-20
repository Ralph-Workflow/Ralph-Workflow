# Template Customization Guide

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


How to customize prompt templates in the Python package.

## Template Locations

**Packaged templates**:
```
ralph-workflow/ralph/prompts/templates/
```

**Workspace overrides** (discovered in order):
- `.agent/prompts/shared/`
- `.agent/prompts/`
- `.agent/prompts/partials/`

**Relevant modules**:
- `ralph-workflow/ralph/prompts/template_registry.py`
- `ralph-workflow/ralph/prompts/template_engine.py`
- `ralph-workflow/ralph/prompts/template_parsing.py`
- `ralph-workflow/ralph/prompts/materialize.py`

## Template Format

The package uses Jinja-based templates (`.jinja`, `.j2`, and `.txt` partials).

**Packaged templates**:
- `planning.jinja`
- `developer_iteration.jinja`
- `review.jinja`
- `fix_mode.jinja`
- `commit_message.jinja`
- `shared/_context_section.jinja`
- `shared/_mcp_tools.jinja`

## Customization Workflow

1. Add or edit a workspace template override in `.agent/prompts/`.
2. Keep variable names aligned with the Python prompt materialization code.
3. Validate from `ralph-workflow/`:

```bash
pytest tests/test_prompts.py -v
pytest tests/test_prompt_template_files.py -v
pytest tests/test_prompt_materialize.py -v
make verify
```

## Legacy Note

Older documentation may refer to Rust source paths such as `ralph-workflow/src/prompts/templates/` or XML/XSD-specific prompt plumbing. Those references are historical and do not describe the maintained Python package.
