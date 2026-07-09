# example-api

The canonical Ralph starter task: a small Flask application that exposes a
single `GET /health` endpoint and ships with one pytest covering it. It is
the smallest possible end-to-end result the Ralph loop can produce, and is
used as the worked reference for a "small feature slice with a visible
endpoint" first task. See the
[proof page](../docs/examples/example-api.md) for the rubric-style
interpretation.

## What this project is

A minimal Flask service:

- An [application factory](https://flask.palletsprojects.com/en/stable/patterns/appfactories/)
  builds an isolated `Flask` instance, so tests can construct fresh apps
  without inheriting module-level globals.
- A [`Blueprint`](https://flask.palletsprojects.com/en/stable/blueprints/)
  (`health_bp`) registers a single `GET /health` route that returns a small
  JSON payload `{"status": "ok"}` with `Content-Type: application/json`.
- One pytest (`tests/test_health.py`) exercises the endpoint with
  `app.test_client()` and asserts the status code, content type, and JSON
  body.

The project satisfies the four acceptance criteria of the canonical
[`ralph-workflow/PROMPT.md`](../ralph-workflow/PROMPT.md) starter template:

1. `GET /health` returns `200`.
2. The response body is valid JSON: `{"status": "ok"}`.
3. `Content-Type` is `application/json`.
4. A test in `tests/` covers the endpoint.

## Project layout

```text
example-api/
├── pyproject.toml         # flask>=3.0, pytest>=7.0, requires-python>=3.12
├── README.md              # this file
├── src/
│   └── api/
│       ├── app.py         # create_app() factory + _BLUEPRINTS tuple
│       └── routes/
│           └── health.py  # health_bp Blueprint + GET /health
└── tests/
    └── test_health.py     # test_health_returns_ok
```

Key conventions:

- `src/api/app.py` exposes `create_app() -> Flask` and a module-level
  `_BLUEPRINTS: tuple[Blueprint, ...] = (health_bp,)`. Adding a new
  blueprint is a single-line change there; no other module needs editing.
- `src/api/routes/health.py` defines `health_bp` and the `GET /health`
  view. The view returns `jsonify({"status": "ok"})` directly — without
  tuple wrapping — so Flask sets `Content-Type: application/json`
  automatically.
- `tests/test_health.py` uses `app.test_client()` and asserts
  `status_code == 200`, `content_type == "application/json"`, and
  `get_json() == {"status": "ok"}`.

## How to run it

The worktree root `.gitignore` already covers `.venv/`, `.pytest_cache/`,
`__pycache__/`, and `*.egg-info/`, so this project does not need its own
`.gitignore`.

```bash
cd example-api
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e '.[test]'
pytest tests/test_health.py -q
```

Expected output:

```text
. 1 passed in 0.0Xs
```

To run the app manually:

```bash
flask --app src.api.app:create_app run --port 5050
curl -i 127.0.0.1:5050/health
# HTTP/1.1 200 OK
# Content-Type: application/json
# ...
# {"status":"ok"}
```

(Note: the `127.0.0.1:5050/health` URL above is the local dev server
— start it with the `flask` command first.)

## What it does NOT prove

This is a deliberately tiny reference surface. It does **not** demonstrate:

- Authentication or authorization.
- Error handling beyond the happy path (no 4xx/5xx paths are covered).
- Persistence, databases, or any stateful behavior.
- Real network I/O — the test uses `app.test_client()` only.
- Multiple endpoints, blueprints beyond `health_bp`, or non-trivial routing.
- Configuration via environment variables, secrets, or external config.
- Logging, metrics, tracing, or observability hooks.

Treat the example as the shape of a "done" starter task, not as a template
for production Flask services.

## Where to go next

- The
  [proof page](../docs/examples/example-api.md)
  explains how to read this example against the documentation rubric.
- The
  [docs map](../docs/README.md)
  routes readers to the maintained Python docs.
- The
  [Ralph starter task template](../ralph-workflow/PROMPT.md)
  is the spec the example was built from.
- The development-analysis decision that confirmed all four AC pass is at
  [`.agent/artifacts/history/development_analysis_decision/20260708T201119_development_analysis_decision.md`](../.agent/artifacts/history/development_analysis_decision/20260708T201119_development_analysis_decision.md).