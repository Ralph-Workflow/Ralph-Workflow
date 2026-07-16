<!-- ralph-policy-schema: v2 -->
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
  unnoticed: either wire it in, or delete it.
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

RALPH-FACT: supported_platforms: linux, macos. Linux is verified via CI (`.woodpecker.yml` runs `python:3.12-bookworm` on Codeberg; `.github/workflows/*.yml` runs `ubuntu-latest`; `Dockerfile` builds on `python:${PYTHON_VERSION}-slim` / `debian:bookworm-slim`). macOS is the maintainer's development environment (POSIX shell, identical bash dialect). The gate-script corpus is shell-only (bash) and POSIX paths only; the existing recipes contain no bash-only constructs without a portable replacement and no hardcoded `/` separators that would break on a future Windows port. The `Operating System :: Microsoft :: Windows` classifier in `ralph-workflow/pyproject.toml` is a packaging-metadata claim for PyPI consumers and is independent of this policy's `supported_platforms`; per the resolution rule above, packaging classifiers do NOT count as CI-tested gate support. Per the Cross-platform section above, the four Windows-portability obligations are NOT binding because Windows is not in `supported_platforms`. Adding Windows to `supported_platforms` requires satisfying all four obligations (no bash-only syntax in wired gates; explicit `.gitattributes` line-ending handling; no hardcoded `/` separators; an exercised Windows CI runner) and re-evaluating this fact in the same workflow.
RALPH-FACT: shell_dialect: bash (every wired gate script is supposed to use `#!/usr/bin/env bash` and `set -euo pipefail` per the Default requirements above; the pre-commit fabrication guard, the Woodpecker-trusted wt028-drift-check script, and the three remote build-server helpers do). The `set -u`-only legacy form is no longer present after wt-038 closed the strict-mode gap on `ralph-workflow/scripts/wt028-drift-check.sh`. The repo-root `scripts/verify_claims.sh` is NOT a wired gate and is NOT in this corpus (see `script_directory` below); the route-page link checker is a Python script under the same root. Two Makefile-recipe violations of the strict-mode contract are recorded as project reality under `known_gate_corpus_defects` below.
RALPH-FACT: script_directory: ralph-workflow/scripts/ is the only directory whose bash script is wired into a `make` gate (`make verify-drift` invokes `bash scripts/wt028-drift-check.sh` from the ralph-workflow Makefile). The repo-root `scripts/` houses the git pre-commit fabrication guard (`scripts/pre-commit`; wired into `.git/hooks/pre-commit`), the route-page link checker (`scripts/check_route_page_links.py`; wired into `make route-linkcheck` and `make policy-citation-linkcheck`, NOT into `make verify`), the three remote build-server helpers (`scripts/remote/{probe,run,sync}.sh`), the social-proof wrapper (`scripts/verify_social_proof.py`; called from the verify chain), and one OUT-OF-CORPUS deployment helper (`scripts/verify_claims.sh`). The OUT-OF-CORPUS status of `verify_claims.sh` is deliberate: the script hard-codes `/home/mistlight/.openclaw/workspace` and expects sibling `Ralph-Site` / `ralph-workflow` / `CLAIMS_LEDGER.md` paths that are absent from a clean clone. It is therefore not reproducible by a developer with a clean checkout and is recorded as project reality (not a gate) under `known_gate_corpus_defects` below; the dev cycle must remove it from the repo root, replace the hard-coded paths with repository-relative or configured inputs, or absorb the helper into a wired gate that satisfies this policy's reproducibility and no-phantom-path requirements.
RALPH-FACT: known_gate_corpus_defects: (1) `ralph-workflow/Makefile:verify-drift` opens its inline shell gate with `bash -c 'set -e; …'` rather than `set -euo pipefail`. A failure earlier in a multi-stage `grep | grep -v | …` pipeline is hidden by the final pipeline command's exit status, contradicting this policy's strict-mode and fail-closed contract. The dev cycle MUST replace `set -e` with `set -euo pipefail` (or move the logic into the already-strict `ralph-workflow/scripts/wt028-drift-check.sh`) and MUST add a black-box test that demonstrates an upstream grep failure cannot pass. (2) `ralph-workflow/Makefile:formula-check` falls through with `echo "Ruby not installed, skipping formula check (install Ruby to enable)"` and exits 0 when `ruby` is absent from `PATH`. The Homebrew formula at `ralph-workflow/Formula/ralph-workflow.rb` is therefore not validated on any machine without Ruby, and the gate reports success while checking nothing — a hollow gate forbidden by this policy. The dev cycle MUST either pin Ruby in the declared `make dev` prerequisite (or in `uv` extras) so the gate can run, OR make `formula-check` exit non-zero with an actionable policy citation when Ruby is unavailable. Both defects are recorded as project reality for the dev cycle to repair; this policy's strict-mode and no-hollow-gate requirements bind regardless.

## AI execution instructions

**Who the gate scripts' failure output is for.** When a gate fails it prints the
policy requirement for fixing the failure, and cites it (see "Failure output").
That instruction is addressed to **development agents working on the project's
code**. It is explicitly NOT addressed to a policy-authoring agent — an agent
whose task is to record this project's policy is FILLING OUT A FORM, and must not
be diverted into repairing the project's tests, types, or lint findings because a
script it probed told it to. A policy-authoring agent that runs a gate as a
bounded probe reads only ONE thing from the result: did the gate RESOLVE. It
ignores the citation, the fix instructions, and the exit code.

To follow this policy, an agent writing or changing a gate script MUST:

* VERIFY every tool, flag, package, and path the script references actually
  exists before declaring the script done. Never invent one.
* GIVE the script the exit-code contract and strict mode required above.
* MAKE the script print, on failure, the governing policy requirement and a
  followable citation to the policy file that states it — and on timeout, the
  time-budget requirements specifically.
* COVER the script with a test that proves it both passes AND fails correctly.
* WIRE the script into the authoritative verification entry point in the same
  change that creates it.
* CHECK the script against `supported_platforms` before claiming compliance.
* UPDATE this policy's facts in the same workflow that changes the shell
  dialect, the script directory, or the supported platforms.

An agent MUST NOT:

* Write a gate script that fails open, or that exits 0 on internal error.
* Invent a tool, flag, or dependency the project does not have.
* Create a hollow gate solely to satisfy a policy requirement.
* Weaken, disable, or delete a gate script so that a failing change passes.
* "Fix" a working gate script because the checks it runs report real failures.
  A red gate reporting a real problem is the gate doing its job.
* Reword, condense, or relax the requirement sections of this policy. They are
  the normative standard, not a draft.

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
and by black-box tests under `ralph-workflow/tests/` (e.g.
`tests/test_check_route_page_links.py`,
`tests/display/test_single_mode_anti_drift.py`).

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
  review date: 2026-07-13

* publisher: OpenSSF
  title: "Source Code Management Best Practices"
  http: https://best.openssf.org/SCM-BestPractices/
  review date: 2026-07-13

* publisher: ShellCheck
  title: "ShellCheck Wiki"
  http: https://www.shellcheck.net/wiki/
  review date: 2026-07-13

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
* Schema version: `<!-- ralph-policy-schema: v2 -->`
