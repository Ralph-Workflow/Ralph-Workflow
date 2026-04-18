# Project Code Style Guide

This directory has not yet been fully ported to the Python implementation. Many files here still document the retired Rust codebase.

## Current Python style source of truth

Use these instead when working on the maintained package:

- `ralph-workflow/pyproject.toml` — tool configuration (`ruff`, `mypy`, packaging)
- `ralph-workflow/CONTRIBUTING.md` — contributor workflow
- public module docstrings under `ralph-workflow/ralph/` — API expectations for pydoc
- existing Python modules/tests — preferred naming, typing, and error-handling patterns

## Python style expectations

- Type all public functions and exported APIs.
- Keep public module and package docstrings self-sufficient so `pydoc` is useful without extra Markdown.
- Prefer small, explicit modules over deeply clever abstractions.
- Keep CLI, orchestration, MCP, workspace, and Git layers clearly separated.
- Match the existing `ruff`/`mypy`/`pytest`-driven development flow.

## Legacy status

If a file in this directory talks about Rust-only topics such as crates, clippy, dylint, `#[allow(...)]`, or functional-Rust boundary rules, treat it as archival background until it is rewritten for Python.