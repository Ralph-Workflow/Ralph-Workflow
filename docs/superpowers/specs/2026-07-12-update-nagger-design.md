# Update nagger design

Status: approved (2026-07-12)

## Goal

Tell the user when a newer `ralph-workflow` release exists and suggest the
right way to upgrade for their install method. This is a nagger, not an auto
updater: it never mutates anything, it only informs.

## Non-goals

- No auto-download, no self-update, no writing to the install location.
- No Homebrew support (Ralph Workflow is not yet distributed via brew).
- No blocking of a run: every failure path is silent and best-effort.

## Behavior summary

- On **every** `ralph` run, if the cached state says a newer version is
  available and the check is not opted out, print one prominent line at run
  start with the current -> latest versions and the upgrade command for the
  detected environment.
- Under `ralph --diagnose`, print a fuller version block (current version,
  latest known, detected install method, upgrade command).
- The network refresh against PyPI is throttled to at most once per 24h and
  runs in a background daemon thread, so run start is never delayed. The nag
  itself is driven by the cache, so the first sighting of a new release may lag
  by one invocation. That is acceptable.

## Architecture

New focused package `ralph/update_check/`, following the repo's small-module
convention. Public surface lives in `__init__.py`; everything else is an
internal helper module.

### `pypi.py`

- `fetch_latest_version(client, *, timeout=2.0) -> str | None`
- Best-effort `httpx` GET to `https://pypi.org/pypi/ralph-workflow/json`.
- Reads `info.version` from the JSON body.
- Swallows every exception (network, timeout, JSON, schema) and returns `None`.
- The `httpx` client is injectable for testing; production uses a short-lived
  client with a 2s timeout.

### `state.py`

- Cache file at `$XDG_CACHE_HOME/ralph-workflow/version-check.json`, falling
  back to `~/.cache/ralph-workflow/version-check.json`.
- Stored fields: `last_checked` (unix ts, float), `latest_version` (str).
- `load_state()` / `save_state()` tolerate missing/corrupt files (treated as
  empty state).
- `is_refresh_due(state, now, *, ttl_seconds=86400) -> bool` implements the
  24h throttle. A clock is injected (`now`) for testing.

### `environment.py`

- `detect_install(*, argv0, package_file, environ, platform_system, is_frozen,
  filesystem) -> InstallInfo` — all inputs injected so the function is pure and
  testable.
- `InstallInfo` carries a `kind` enum and an `upgrade_command` string (or a
  short multi-line hint for the frozen/docker/unknown cases).
- Detection order (first match wins):
  1. **Frozen bundle** — `is_frozen` (i.e. `getattr(sys, "frozen", False)` /
     `sys._MEIPASS`). Hint points to the releases/download page.
  2. **Source checkout / editable** — `package_file` resolves inside a git work
     tree that contains the package (a `.git` directory is found by walking
     upward and the tree holds the `ralph-workflow` source). Resolve the repo
     root and emit `cd "<repo root>" && git pull origin main` so we cd there
     for the user even if they forgot where the checkout lives.
  3. **pipx** — install path under a pipx venvs dir (`PIPX_HOME` or the default
     `~/.local/pipx/venvs/...`). Emit `pipx upgrade ralph-workflow`.
  4. **uv tool** — install path under the uv tools dir
     (`~/.local/share/uv/tools/...` or the platform equivalent). Emit
     `uv tool upgrade ralph-workflow`.
  5. **Docker** — `/.dockerenv` exists. Hint: re-pull the image.
  6. **plain pip** (fallback for a detectable Python install) — emit
     `pip install --upgrade ralph-workflow`.
  7. **unknown** — environment or OS undetectable. Generic hint: "A newer
     version X is available - see https://pypi.org/project/ralph-workflow/".
- `platform_system` (`platform.system()`) is only used for OS-specific
  phrasing; whenever it is unknown, fall through to the generic hint.

### `gating.py`

- `is_update_check_disabled(environ, config) -> bool` returns True if **any**
  of:
  - `RALPH_DISABLE_TELEMETRY` truthy (reuse `telemetry._sentry`'s truthy set
    and `is_telemetry_disabled`),
  - `telemetry_enabled = false` in config (reuse
    `telemetry._sentry.is_telemetry_disabled_by_config`),
  - `RALPH_DISABLE_UPDATE_CHECK` truthy (new, same truthy set),
  - `update_check_enabled = false` in config (new key).
- Rationale: a version check is an outbound call, so honoring the telemetry
  opt-out is correct; the dedicated knob lets a user silence the nag without
  giving up telemetry.

### `compare.py`

- `is_newer(current: str, latest: str) -> bool` using `packaging.version.parse`
  (PEP 440). Returns False on any parse error. Pre-release latest over a stable
  current is treated per PEP 440 ordering (a pre-release is not "newer" than the
  matching final, and `parse` handles the rest).
- Adds `packaging` as a runtime dependency in `pyproject.toml`.

### `__init__.py` (public surface)

- `maybe_render_update_nag(display_context, *, deps=...) -> None`
  - Returns immediately (no output, no network) if
    `is_update_check_disabled(...)`.
  - Kicks off the background refresh thread if a refresh is due.
  - Reads the cache; if `is_newer(current, cached_latest)`, renders the
    one-line nag via the display context.
- `update_status(*, deps=...) -> UpdateStatus`
  - Synchronous-friendly summary for `--diagnose`: current version, latest
    known (from cache), whether an update is available, detected install kind,
    and the upgrade command. Does not spawn a thread; `--diagnose` may trigger a
    direct (still time-boxed, still best-effort) refresh so the diagnostic
    output reflects current state.

## Wiring

- Run path: call `maybe_render_update_nag(...)` from the CLI callback in
  `ralph/cli/main.py` at run start (after display context is built, before the
  pipeline runs). One `↑`-prefixed line, e.g.:

  ```
  ↑ Ralph Workflow 0.9.1 available (you have 0.8.24)
    upgrade: pipx upgrade ralph-workflow
  ```

- Diagnose path: add a version block to `ralph/cli/commands/diagnose.py` using
  `update_status(...)`.

## Error handling

- Every outbound and filesystem operation is wrapped so no failure ever
  propagates to the user's run. Worst case: no nag is shown.
- A corrupt or unreadable cache file is treated as empty state and overwritten
  on the next successful refresh.
- The background thread is a daemon and never joined; if it dies, the run is
  unaffected.

## Testing

Pure unit tests, no real network and no real sleeping:

- `pypi.py`: injected fake client returns good JSON, bad JSON, HTTP error,
  timeout -> assert version or `None`.
- `state.py`: round-trip save/load; missing file; corrupt file; `is_refresh_due`
  boundaries with an injected clock.
- `environment.py`: one test per detection branch by faking `is_frozen`,
  `package_file` location, `environ` (pipx/uv markers), `/.dockerenv` via the
  injected filesystem, and `platform_system`. Assert both `kind` and
  `upgrade_command` (including the resolved `cd` path for source checkouts).
- `gating.py`: each of the four opt-out signals independently disables; none set
  -> enabled.
- `compare.py`: newer, older, equal, pre-release vs final, unparseable input.
- `__init__.py`: nag suppressed when disabled; nag rendered when cache shows a
  newer version; no nag when up to date; refresh thread scheduled only when due.

## Files touched

- New: `ralph/update_check/{__init__,pypi,state,environment,gating,compare}.py`
- New: `ralph-workflow/tests/update_check/...`
- Edit: `ralph/cli/main.py` (run-start nag), `ralph/cli/commands/diagnose.py`
  (version block), `pyproject.toml` (add `packaging`).
