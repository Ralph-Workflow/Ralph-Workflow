# Contributing to Ralph Workflow (Python)

This directory contains the maintained Python package.

## Development setup

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
make dev
```

To refresh the runnable `ralph` executable from the current checkout, run:

```bash
make install
```

When adding or renaming fields on `UnifiedConfig` / `GeneralConfig` in `ralph/config/models.py`, also update the bundled user-global template at `ralph/policy/defaults/ralph-workflow.toml` so new users see the documented default.

## Required verification

Run this before opening or updating a PR:

```bash
make verify
```

The dead-code audit is available separately while the existing dead-code backlog is still being cleaned up:

```bash
make dead-code
```

`make dead-code` uses Vulture and is expected to fail until the repo is fully cleaned. Keep it separate from `make verify` for now so the tooling can be validated without blocking unrelated work.

You can narrow failures with:

```bash
ruff check ralph/ tests/
ruff format --check ralph/ tests/
uv run python -m mypy ralph/
uv run python -m ralph.verify_timeout --suite-timeout 30 -- pytest tests/ -q -n 8 --cov=ralph --cov-report=term-missing --cov-report=html --cov-fail-under=80
make test
make test-unit
make test-integration
```

## Documentation expectations

- Update user-facing Markdown when workflows or commands change.
- Update public module/package docstrings when APIs change.
- Keep exported package docstrings self-sufficient enough for `pydoc` users.
- New public subpackages added under `ralph/` must have an `.. automodule::` entry in `docs/sphinx/modules.rst`. The test `tests/test_sphinx_modules_coverage.py` enforces this — update `_EXCLUDED` in that test if the subpackage is intentionally private.
- When changing pipeline hardening around artifacts or agent success criteria, document both the behavior change and the failure mode it prevents. Future contributors need to understand why the stricter contract exists.

## Agent hardening contract

When working on `ralph/pipeline/runner.py`, `ralph/phases/`, or Claude/CCS agent invocation, preserve these invariants unless you are deliberately replacing them with something stronger:

1. A clean subprocess exit is not enough evidence of useful work for `review`; `development` and `fix` must still produce real workspace side effects, not empty no-op runs.
2. `review` depends on a fresh per-phase artifact created during the current invocation; `development` and `fix` may emit artifacts or handoffs, but pipeline success must not depend on them.
3. The runner clears stale per-phase artifacts before invoking the agent so interrupted runs cannot satisfy later checks accidentally or leak old summaries into later phases.
4. Claude/CCS MCP invocations must avoid half-configured tool restriction flags. If the live MCP allowlist cannot be discovered, prefer the safer strict-MCP path over emitting brittle `--tools ""` combinations.

This logic is more complex than a naive "agent exited 0" flow, but it exists to prevent silent no-op runs in unattended mode without forcing side-effect-driven phases to produce busywork artifacts. If you change it, update tests and docs together.

## MCP multimodal compatibility contract

When modifying the MCP tool surface, maintain these invariants:

### Existing text-only tools unchanged

All existing MCP tools (`read_file`, `write_file`, `list_directory`, etc.) must continue returning text content blocks with the same JSON shape. Any change that alters the wire format of existing text tools is a breaking change.

### Multimodal support is opt-in

The `read_image` tool and associated `MediaRead` capability:

- Default to disabled (`media.enabled = false`)
- Require explicit opt-in via `[media]` section in `mcp.toml`
- Are gated at registration time (tool only registered when `media.enabled = true`)

### Client capability filtering

When a client sends `initialize` without declaring multimodal support (`capabilities.image`, `capabilities.media`, or `capabilities.multimodal`), multimodal-only tools must not appear in `tools/list`.

This is enforced by:
1. Capturing client capabilities from the `initialize` params at connection time
2. Filtering tools marked `is_multimodal=True` when building `tools/list` for text-only clients

### Upstream multimodal boundary

When an upstream MCP server returns a non-text content block, Ralph must reject it with a clear error rather than silently stringify or drop the block. The error message must identify the server, tool, and block type.

This prevents silent data loss in text-only downstream flows and makes incompatibility visible rather than implicit.

### Dead code policy

Any MCP code that is proven unused during feature work must be either:

1. Wired into the maintained runtime with tests proving real use, or
2. Deleted along with its imports and stale tests/docs

Do not leave "reserved for later" MCP scaffolding behind. If in doubt, remove it — it can be restored from git if needed later.

## Recovery architecture contract

Recovery, failure classification, retry counting, and chain fallover each have a single conceptual owner in `ralph/recovery/`. Extend the owner, do not add handlers at call sites. New failure modes are added by extending the `FailureClassifier` in `ralph/recovery/classifier.py`, not by sprinkling classification logic at invoke sites.

## Release & Versioning

For the complete release process — version bumping, building, validating, and publishing
to PyPI — see [docs/sphinx/versioning.md](docs/sphinx/versioning.md).

For local validation only:

```bash
cd ralph-workflow
rm -rf dist
uv run hatch build
uv run python -m twine check dist/*
```
