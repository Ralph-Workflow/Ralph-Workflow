# Ralph Workflow (Python)

Ralph Workflow is a **free and open-source** Python 3.12+ CLI for **AI agent orchestration** on your own machine.
It extends the simple Ralph loop into a **composable loop framework** for real software engineering, and the default workflow is already strong enough to start with before you customize anything.

This README is the **install + operator entrypoint**, not the main product pitch.
It intentionally leaves out deeper material that belongs in the manual and developer docs.

## Use this route

1. [START_HERE.md](../START_HERE.md)
2. [docs/README.md](../docs/README.md)
3. [docs/sphinx/index.rst](docs/sphinx/index.rst)

## Install

```bash
pipx install ralph-workflow
ralph --help
```

## Verification

When you change Ralph Workflow itself, the canonical repo-level verification command is:

```bash
make verify
```

## Operator docs

- [Getting Started](docs/sphinx/getting-started.md)
- [Quickstart](docs/sphinx/quickstart.md)
- [Configuration](docs/sphinx/configuration.md)
- [Reference](docs/sphinx/reference.md)
- [User stories](docs/sphinx/user-stories.md)

## Deeper material

- [Developer Reference](docs/sphinx/developer-reference.md)
- [Modules index](docs/sphinx/modules.rst)
