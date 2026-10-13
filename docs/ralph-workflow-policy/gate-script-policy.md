<!-- ralph-policy-schema: v4 -->
<!-- ralph-policy-id: gate-script-policy.md -->

# Gate Script Policy

## Purpose and scope

This policy governs the **scripts and commands that implement the project's
gates** — the shell scripts, batch files, task-runner targets, and helper
programs that a verification, lint, typecheck, test, or CI gate actually
executes. It defines what such a script must look like, which platforms it
must run on, how it reports success and failure, how it is tested, and how it
is wired into the authoritative verification entry point.

Read this policy when changing the build process, a gate, a gate script, or
CI. It does NOT apply to application source code — the clean-code, linting,
and testing policies own that.

A gate script is code. It is held to the same standard as the code it checks.

## Default requirements

* **Exit-code contract.** A gate script MUST exit 0 on pass and non-zero on
  fail. It MUST fail **closed**: an unexpected error inside the script is a
  gate FAILURE, never a silent pass. A script that swallows an error and exits
  0 is worse than no gate at all, because it manufactures false confidence.
* **Strict mode.** A shell gate script MUST enable the strongest available
  error handling for its dialect — for `bash`/`sh`, `set -euo pipefail`; for
  PowerShell, `$ErrorActionPreference = 'Stop'`. An unset variable or a failed
  stage of a pipeline MUST NOT be silently ignored.
* **Bounded.** Every gate MUST have a timeout. A gate that can hang forever is
  a broken gate.
* **Deterministic and offline.** A gate MUST NOT depend on network access
  unless it is explicitly declared as requiring it. A gate whose result
  depends on the weather of the internet is not a gate.
* **No phantom dependencies.** Every tool, library, package, flag, and path a
  gate script invokes MUST actually exist, and every tool it needs MUST be
  declared as a prerequisite. A script that calls a command nobody installed,
  or passes a flag the tool does not have, is a BROKEN SCRIPT — not a broken
  environment. Inventing a plausible-looking tool, flag, or package name is
  fabrication and is forbidden.
* **Reproducible by a human.** A developer with a clean clone who follows the
  declared prerequisites MUST be able to run every gate. An undeclared setup
  step is a defect in this policy, not a failing of the developer.
* **Scripts are tested code.** Every gate script MUST be covered by a test
  that drives it as a black box through its real entry point, asserting both
  that it passes when it should AND that it FAILS when it should. A gate whose
  failure path was never exercised is an untested gate. A script too tangled
  to test is a script to refactor.
* **Wired in or deleted.** Every gate script MUST be reachable from the
  authoritative verification entry point declared in the verification policy.
  A check that runs only in an opt-in suite the default gate excludes WILL rot
  unnoticed: either wire it in, declare it as a named profile in the verification
  policy (Suite admission NAMED PROFILE lane) with its own owner and trigger, or
  delete it. An undeclared named profile is an unrouted criterion and remains a
  violation of this rule.
* **No hollow gates.** A script that technically exits 0 while verifying
  nothing — `echo ok`, a test target matching zero tests, a linter pointed at
  an empty directory — is non-compliant. The gate MUST actually exercise the
  thing it claims to check.
* **A gate script owns only its own correctness.** The script MUST be correct.
  Whether the checks it RUNS pass is the project's concern, not the script's.
  A script that exits non-zero because the tests it invoked legitimately failed
  is a WORKING script and MUST NOT be "fixed" to make the build green.

## Failure output

A gate that fails silently, or that fails with nothing but a stack trace, wastes
the time of every agent and human who hits it. **A failing gate MUST teach the
reader how to fix it, and MUST cite the policy that says so.**

* **On any failure**, a gate script MUST print: what failed, and the policy
  requirement that governs fixing it, cited by path and section — for example
  `docs/ralph-workflow-policy/testing-policy.md § AI execution instructions`.
  The citation is not decoration: it is how an agent that hits a red gate finds
  the rule it must satisfy, without loading every policy into context.
* **On a TIMEOUT specifically**, a gate script MUST print the policy requirements
  governing time budgets — the verification time budget, its enforcement
  mechanism, and the rule that a slow gate is a DEFECT to be diagnosed, never a
  budget to be raised — and cite the policy that states them. A timeout is the
  one failure mode most likely to be "fixed" by weakening the gate, so the
  correct rule must be in front of the reader at the moment it happens.
* The failure output MUST name the specific policy file, not "the policy". A
  citation the reader cannot follow is not a citation.
* The output MUST be actionable: the requirement that was violated and the
  outcome that satisfies it, not a restatement of the error.

**This output is addressed to development agents and humans working on the
project's code.** It is NOT addressed to the policy-authoring agents that write
these documents: an agent whose task is to fill out a policy form must not be
diverted into fixing a failing test because a gate script told it to. See the AI
execution instructions below.

## Security

* Secrets MUST NOT be embedded in a gate script, and MUST NOT be passed on a
  command line, where they leak into process listings and CI logs. Use the
  environment or the platform's secret store.
* Piping a remote payload straight into an interpreter (`curl … | sh`) is
  forbidden. Tool installs MUST be pinned to a version and, where the ecosystem
  supports it, verified by checksum or signature.
* A gate MUST NOT execute unpinned remote code, and MUST NOT fetch from the
  network without an explicit, bounded timeout.
* Temporary files MUST be created with restrictive permissions in a private
  directory. World-writable temp paths are a local privilege-escalation
  surface.
* A gate script MUST NOT weaken repository security to pass: it MUST NOT
  disable signature verification, skip a hook, or introduce a bypass flag.

## Cross-platform

The platforms a gate must run on are recorded as `supported_platforms` and
resolved from real project evidence — the CI matrix, `.gitattributes`, the
presence of `.ps1`/`.cmd` scripts, packaging metadata — never assumed.

**If `supported_platforms` includes Windows, the following bind:**

* Gate scripts MUST NOT rely on bash-only syntax or POSIX-only utilities that
  are absent on Windows, or MUST ship a maintained, tested Windows equivalent
  invoked through the same task-runner target.
* Line endings MUST be handled explicitly and committed as configuration
  (`.gitattributes`). A script that breaks under a CRLF checkout is broken.
* Path separators MUST NOT be hardcoded to `/`, and paths containing spaces
  MUST be quoted.
* The gate MUST actually be exercised on Windows in CI. A Windows support
  claim that no CI job ever runs is unverified, and is treated as a defect.

**If `supported_platforms` does not include Windows**, the four requirements
above do not apply. They begin to apply the moment the project adds Windows to
`supported_platforms` (a Windows CI runner, a maintained Windows-equivalent
gate corpus, or an explicit declaration in the `supported_platforms` fact
below). A Windows packaging classifier in `pyproject.toml` alone is NOT a
`supported_platforms` change; it is a PyPI-installation hint and does not
count as CI-tested gate support.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely on
them when enforcing this policy and MUST keep them current as the project
evolves.

RALPH-FACT: supported_platforms: linux, macos. Linux is verified via CI (`.github/workflows/verify.yml` runs `ubuntu-latest` on the GitHub primary repository; `.woodpecker.yml` continues `python:3.12-bookworm` on the Codeberg mirror; `Dockerfile` builds on `python:${PYTHON_VERSION}-slim` / `debian:bookworm-slim`). macOS is the maintainer's development environment (POSIX shell, identical bash dialect). The checkout `make install` / `make dev` workflow is likewise POSIX/WSL only; native Windows users install the packaged application rather than running the shell checkout installer. The gate-script corpus is shell-only (bash) and POSIX paths only; the existing recipes contain no bash-only constructs without a portable replacement and no hardcoded `/` separators that would break on a future Windows port. The `Operating System :: Microsoft :: Windows` classifier in `ralph-workflow/pyproject.toml` is a packaging-metadata claim for PyPI consumers and is independent of this policy's `supported_platforms`; per the resolution rule above, packaging classifiers do NOT count as CI-tested gate support. Per the Cross-platform section above, the four Windows-portability obligations are NOT binding because Windows is not in `supported_platforms`. Adding Windows to `supported_platforms` requires satisfying all four obligations (no bash-only syntax in wired gates; explicit `.gitattributes` line-ending handling; no hardcoded `/` separators; an exercised Windows CI runner) and re-evaluating this fact in the same workflow.
RALPH-FACT: shell_dialect: bash (every wired gate script is supposed to use `#!/usr/bin/env bash` and `set -euo pipefail` per the Default requirements above; the pre-commit fabrication guard, the Woodpecker-trusted wt028-drift-check script, and the three remote build-server helpers do). The `set -u`-only legacy form is no longer present after wt-038 closed the strict-mode gap on `ralph-workflow/scripts/wt028-drift-check.sh`. The repo-root `scripts/verify_claims.sh` is NOT a wired gate and is NOT in this corpus (see `script_directory` below); the route-page link checker is a Python script under the same root. The repaired formula gate now fails closed when Ruby is unavailable (see `formula_check_gate` below); the remaining out-of-corpus deployment-helper defect is recorded under `known_gate_corpus_defects` below.
RALPH-FACT: script_directory: ralph-workflow/scripts/ is the only directory whose bash scripts are wired into `make` gates, and ALL THREE scripts in it are wired: `scripts/verify_drift.sh` (invoked by `make verify-drift` from the ralph-workflow Makefile and itself delegating to `scripts/wt028-drift-check.sh`) and `scripts/packaging_operations.sh` (invoked with a subcommand argument by the `dist-homebrew`, `packaging-smoke`, and `formula-check` Make recipes — ralph-workflow/Makefile:299,318,334); `make verify` requires both the `verify-drift` and `formula-check` targets from the ralph-workflow Makefile. The repo-root `scripts/` houses the git pre-commit fabrication guard wired at `core.hooksPath = .githooks` — the active hook is `.githooks/pre-commit` (installed by the repo-root `make setup-hooks` target at `Makefile:10-15`, which sets `core.hooksPath = .githooks` and is the only wiring the project ships); the hook runs `python3 scripts/verify_social_proof.py` against staged markdown (`.githooks/pre-commit:12`). The legacy Level-1 fabrication guard `scripts/pre-commit` exists at the repo root and has its own `set -euo pipefail` strict-mode header, but is NOT the wired hook — `.git/hooks/` contains no non-sample hooks and the active hook path is `.githooks/`. The repo-root `scripts/` also houses the route-page link checker (`scripts/check_route_page_links.py`; wired into `make route-linkcheck` and `make policy-citation-linkcheck`, NOT into `make verify`), the three remote build-server helpers (`scripts/remote/{probe,run,sync}.sh`), the social-proof wrapper (`scripts/verify_social_proof.py`; called from the verify chain), and one OUT-OF-CORPUS deployment helper (`scripts/verify_claims.sh`). The OUT-OF-CORPUS status of `verify_claims.sh` is deliberate: the script hard-codes `/home/mistlight/.openclaw/workspace` and expects sibling `Ralph-Site` / `ralph-workflow` / `CLAIMS_LEDGER.md` paths that are absent from a clean clone. It is therefore not reproducible by a developer with a clean checkout and is recorded as project reality (not a gate) under `known_gate_corpus_defects` below; the dev cycle must remove it from the repo root, replace the hard-coded paths with repository-relative or configured inputs, or absorb the helper into a wired gate that satisfies this policy's reproducibility and no-phantom-path requirements.
RALPH-FACT: known_gate_corpus_defects: `scripts/verify_claims.sh` hard-codes `/home/mistlight/.openclaw/workspace` and expects sibling `Ralph-Site` / `ralph-workflow` / `CLAIMS_LEDGER.md` paths (`scripts/verify_claims.sh:6-9`), is NOT wired into any `make` target, CI workflow, or `ralph-workflow/Makefile` (verified via `grep -rn "verify_claims" Makefile ralph-workflow/Makefile .github/workflows/ .woodpecker.yml` returns empty), and is therefore not reproducible by a developer with a clean checkout — a missing-phantom-path defect forbidden by this policy's "No phantom dependencies" and "Reproducible by a human" requirements. The dev cycle MUST either remove it from the repo root, replace the hard-coded paths with repository-relative or configured inputs, or absorb the helper into a wired gate that satisfies this policy. This defect is recorded as project reality for the dev cycle to repair; the script's existence as a tracked file does not grant it corpus membership.

RALPH-FACT: formula_check_gate: `make -C ralph-workflow formula-check` invokes `scripts/packaging_operations.sh formula-check`, which runs `ruby -c Formula/ralph-workflow.rb`. Ruby absence emits an actionable citation to this policy's Default requirements and returns 127; an invalid formula propagates Ruby's non-zero status. `make -C ralph-workflow verify` requires this gate before docs, drift, and Python verification; GitHub CI provisions Ruby 3.3.6 via `ruby/setup-ruby@v1`, and the maintained Codeberg image installs Ruby before `make verify`. Black-box subprocess coverage in `ralph-workflow/tests/test_packaging_operations.py` exercises missing-Ruby, successful-Ruby, and rejected-formula behavior in the declared subprocess-e2e profile.

## Guard inventory

| Guard | Owner | Protected outcome | Retirement / review trigger |
|---|---|---|---|
| Exit-code contract | Gate script author | Gate reports accurate pass/fail; no false confidence from swallowed errors | Shell dialect change; new platform added to `supported_platforms` |
| Strict mode | Gate script author | Unset variables and pipeline failures fail the gate, not silently | ShellCheck pinning in `ralph-workflow/pyproject.toml` |
| Bounded | Gate script author | Gate cannot hang forever; 60 s combined budget is respected | New default-lane cost added |
| Deterministic and offline | Gate script author | Gate result is stable and reproducible; no weather dependency | Network-backed check is declared as a named profile |
| No phantom dependencies | Gate script author | Every tool, flag, package, and path the script calls actually exists | Script directory or task runner changes |
| Reproducible by a human | Gate script author | A developer with a clean clone can run every gate | New gate added or existing gate moved |
| Scripts are tested code | Gate script author | Gate failure path is exercised; untested gates are detected | Test coverage gap found; test file added or removed |
| Wired in or deleted | Gate script author | Every gate is reachable from the authoritative entry point; no rot in opt-in suites | Named profile declared, wired in, or deleted |
| No hollow gates | Gate script author | Gate verifies something real; no `echo ok` gates | Gate corpus review |
| Gate script owns only its own correctness | Gate script author | Working gate is not "fixed" to make a failing project pass | Gate script edited to paper over project failures |
| Failure output | Gate script author | Failing gate teaches the reader how to fix it and cites the governing policy | Gate failure message is unfollowable or missing citation |
| Timeout output | Gate script author | Timeout cites time-budget policy; is not "fixed" by weakening the gate | Gate times out without citing policy |
| Security — no embedded secrets | Gate script author | Secrets do not leak into process listings or CI logs | New gate added; CI log review |
| Security — no piping remote code | Gate script author | Gate does not fetch and execute unverified remote code | New install command added |
| Security — no unpinned remote code | Gate script author | Gate fetches only versioned, verified code with a bounded timeout | New network dependency added |
| Security — temp file permissions | Gate script author | Temp files are not world-writable | New temp file creation added |
| Security — no security weakening | Gate script author | Gate does not disable signature verification, skip hooks, or add bypass flags | Gate corpus review |
| Cross-platform | Gate script author | Gate runs correctly on all `supported_platforms` without POSIX-only assumptions | `supported_platforms` changes |
| Windows-specific bindings | Gate script author | Windows gate exercises CRLF handling, path quoting, no hardcoded `/`, and is tested in CI | Windows added to `supported_platforms` |

## AI execution instructions

Shared execution rules (truthful evidence, run every declared gate and report
actual outcome, do not weaken gates, update facts in the same workflow, honest
failure reporting) live in `docs/ralph-workflow-policy/execution-kernel.md` (v1).
This file adds only its domain outcomes, facts, triggers, and exceptions. When
a rule in this file conflicts with the kernel, the kernel wins and this file
is amended.

## Verification

RALPH-PENDING: shellcheck ralph-workflow/scripts/ (assumed 2026-07-15); review trigger: once shellcheck is pinned as a dev dependency in `ralph-workflow/pyproject.toml` and a `make` target is added and wired into `make -C ralph-workflow verify`.

A shell-script linter is the binding enforcement for the strict-mode
requirement above, and ShellCheck is the de facto standard. The current
project state — ShellCheck is absent from `ralph-workflow/pyproject.toml`,
absent from the `make dev` prerequisite, and absent from
`make -C ralph-workflow verify` — means the shell-script corpus is
NOT linted today. This is a deferred gate: the lint rule still binds,
and any agent that adds or edits a bash script MUST run ShellCheck
locally before declaring the change complete, but the canonical
verification suite does not yet exercise it. Until the trigger fires,
bash-script quality is enforced by the wt-038 drift-check script
(which runs every shell script under its own fail-closed contract)
and by black-box coverage under `ralph-workflow/tests/`: `tests/test_check_route_page_links.py` drives the route-link checker. The shell chain is separately checked by the wired `make -C ralph-workflow verify-drift` target; do not claim an unverified dedicated test path.

## Exceptions

A documented exception — a vendored third-party script that cannot be modified,
or a gate that legitimately requires network access — requires a documented
rationale, scope, owner, and removal or review date. Undocumented exceptions
are non-compliant. A script exempted from the lint gate MUST still be tested.

## Maintenance triggers

This policy's FACTS MUST be reviewed in the same workflow as any of:

* A gate script is added, removed, or moved.
* The shell dialect or task runner changes.
* `supported_platforms` changes (a platform is added to or dropped from CI).
* The authoritative verification entry point changes.

## Research basis

* publisher: Google
  title: "Shell Style Guide"
  http: https://google.github.io/styleguide/shellguide.html
  review date: 2026-08-09

* publisher: OpenSSF
  title: "Source Code Management Best Practices"
  http: https://best.openssf.org/SCM-BestPractices/
  review date: 2026-08-09

* publisher: ShellCheck
  title: "ShellCheck Wiki"
  http: https://www.shellcheck.net/wiki/
  review date: 2026-08-09

## Living document contract

This policy is a living document in its FACTS. The `RALPH-FACT:` lines and the
`RALPH-COMMAND:` line track verified project reality and MUST be updated when
that reality changes (new platform, new task runner, new script directory).
Conflicts between this file's recorded facts and the project's established
practice are resolved in favor of the existing project policy — adapt the
facts to verified project reality, never the reverse.

Its REQUIREMENT sections are not. They are the normative standard this project
holds gate scripts to, and they are deliberately stricter than what a project
may currently practice:

* A looser existing project practice is NOT a conflict to resolve in the
  project's favor. It is a gap to close.
* An amendment MUST NOT subvert the INTENT of this policy. Weakening,
  disabling, or deleting a requirement so that a failing change passes is
  forbidden.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: gate-script-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v4 -->`
