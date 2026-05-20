# Ralph Workflow (Python)

Ralph Workflow is a **free and open-source** Python 3.12+ CLI for **AI agent orchestration** on your own machine.
It extends the simple Ralph loop into a **composable loop framework** for real software engineering, and the default workflow is already strong enough to start with before you customize anything.

This README is the **install + operator entrypoint**. It intentionally leaves out deeper material so the first screen stays onboarding-focused.

## Use this route

1. [START_HERE.md](../START_HERE.md)
2. [docs/README.md](../docs/README.md)
3. [docs/sphinx/index.rst](docs/sphinx/index.rst)

Use it for engineering tasks that are **too big to babysit and too risky to trust blindly**.

## Install

```bash
pipx install ralph-workflow
ralph --help
```

## First-run operator docs

- [Getting Started](docs/sphinx/getting-started.md)
- [Quickstart](docs/sphinx/quickstart.md)
- [Configuration](docs/sphinx/configuration.md)
- [Reference](docs/sphinx/reference.md)
- [User stories](docs/sphinx/user-stories.md)

## Deeper material

If you need the fuller manual, configuration detail, or maintainer-facing internals, go to `docs/sphinx/`.
In particular:

- `docs/sphinx/quickstart.md`
- `docs/sphinx/developer-reference.md`
- `docs/sphinx/modules.rst`

## Verification

Use the canonical verification workflow:

```bash
make verify
```
