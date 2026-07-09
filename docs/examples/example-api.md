# Example API proof page

This page is a **proof / example** page in the sense of the
[documentation rubric](../code-style/documentation-rubric.md). It walks
through the small Flask `GET /health` project at
[`example-api/`](../../example-api/README.md) as the canonical Ralph
starter task, and tells you what that project does and does not
demonstrate.

## What this page proves

The example proves that a focused starter task can be run unattended and
produce a reviewable, tested change. Concretely:

- A **bounded task** with one visible endpoint and one test can be
  specified in a few sentences and executed end to end without
  intervention.
- The Flask **application-factory pattern** (`create_app() -> Flask`)
  keeps module-level state empty, so tests can construct isolated app
  instances without inheriting globals.
- A **Blueprint tuple** (`_BLUEPRINTS: tuple[Blueprint, ...] =
  (health_bp,)`) is a one-line extension point: adding a new blueprint
  is a single change in `src/api/app.py` and nothing else.
- A single pytest (`tests/test_health.py`) using `app.test_client()`
  can lock the happy path in place with three assertions: status code,
  `Content-Type`, and JSON body.

## What it does NOT prove

The example is a deliberately minimal reference surface. It does
**not** demonstrate:

- **Authentication** — the route is unauthenticated by design.
- **Error paths beyond 200** — no 4xx or 5xx responses are exercised.
- **Persistence** — no database, no filesystem, no stateful behavior.
- **Real network calls** — the test uses `app.test_client()` only; no
  HTTP server is started.
- **Multiple endpoints** — only one route (`/health`) is registered.
- **Configuration** — no environment variables, secrets, or external
  config.
- **Observability** — no logging, metrics, or tracing.

Treat the example as the shape of a "done" starter task, not as a
template for production Flask services.

## How to interpret the example

The example was built from the canonical
[Ralph starter task template](../../ralph-workflow/PROMPT.md). That
template defines four acceptance criteria. The example satisfies all
four:

1. `GET /health` returns `200`.
2. The response body is valid JSON: `{"status": "ok"}`.
3. `Content-Type` is `application/json`.
4. A test in `tests/` covers the endpoint
   (`tests/test_health.py::test_health_returns_ok`).

The development-analysis decision that confirmed all four criteria
pass is recorded at
[`.agent/artifacts/history/development_analysis_decision/20260708T201119_development_analysis_decision.md`](../../.agent/artifacts/history/development_analysis_decision/20260708T201119_development_analysis_decision.md).
That artifact reports `pytest tests/test_health.py -q` exiting `0`
with "1 passed", the URL map for `create_app()` containing `/health`,
and the JSON body / `Content-Type` matching the spec.

## Where to go next

- Read the
  [example-api README](../../example-api/README.md)
  for the project layout and run commands.
- See
  [Getting started](../../ralph-workflow/docs/sphinx/getting-started.md)
  in the operator manual (specifically the "Pick the right first task"
  section) for how this example fits into a first-run plan.
- The
  [docs map](../README.md)
  routes readers to the maintained Python docs.