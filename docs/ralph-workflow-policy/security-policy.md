<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: security-policy.md -->

# Security Policy

## Purpose and scope

This policy governs how every AI agent working in this project protects
the project against security defects: secret exposure, unsafe handling
of untrusted input, unsafe API usage, and weakened security controls.
It applies to every change that adds or modifies an input surface, a
dependency, a subprocess or filesystem interaction, an authentication
or authorization decision, or any handling of credentials.

Security requirements differ radically by application type: a C library
bans unsafe string functions, a web application defends against CSRF
and injection, a CLI tool guards its subprocess and filesystem
boundaries. This policy is therefore deliberately a FRAME: the "Default
requirements" hold for every project, while the "Threat surfaces"
section carries the project-specific rules and MUST grow and change as
the project does. It does NOT govern physical security, corporate IT
policy, or the security posture of third-party hosted services.

## Default requirements

These requirements hold regardless of application type:

* Secrets (API keys, tokens, passwords, private keys, connection
  strings) MUST NEVER appear in source code, committed configuration,
  logs, error messages, commit messages, or test fixtures. Secrets are
  injected at runtime through the environment or a secret manager.
* All input crossing a trust boundary (network payloads, CLI arguments,
  file contents, environment variables, inter-process messages) MUST be
  treated as untrusted and validated before use. Validation prefers
  allowlists over denylists.
* A security control (scanner gate, validation check, authentication
  step, sandbox restriction) MUST NEVER be weakened, disabled, or
  bypassed to obtain a passing result.
* Security-relevant suppressions (scanner finding waivers, unsafe-block
  annotations, `nosec`-style comments) MUST carry the specific finding
  identifier AND a documented rationale. Blanket suppressions are
  forbidden.
* Facts that cannot be verified from the repository (data sensitivity,
  deployment exposure, threat model) MUST be recorded from the project
  owner's input — never guessed. When owner input is unavailable, the
  fail-safe default applies: record the most conservative plausible
  value (data is sensitive, the deployment is exposed), explicitly
  labeled as an assumption pending owner confirmation.
* Supply-chain security (dependency CVE audits, lockfile
  reproducibility, license checks) is governed by dependency-policy.md;
  this policy governs the code the project itself writes.

## Threat surfaces

Security requirements beyond the defaults are surface-specific. This
section enumerates the project's ACTIVE threat surfaces and the
concrete rules each one carries. Surfaces are added, amended, and
retired as the project evolves; an out-of-date surface list is a policy
violation, reviewed under "Maintenance triggers" below.

RALPH-FACT: active_threat_surfaces: subprocess-and-filesystem surface (the dominant one — Ralph Workflow is a CLI that shells out to AI agent runtimes and reads/writes under a workspace root); AI/agent execution surface (model-generated commands and tool calls — see ralph/mcp/tool/*, ralph/tools/exec*, ralph/process/, ralph/agents/); deserialization of external payloads (subprocess NDJSON output, Claude/OpenCode stream parsers, MCP JSON envelopes); minimal web surface (the embedded MCP HTTP transport per ralph/mcp/server/_fallback_http_handler.py — local-only, not internet-facing). Web/HTTP applications of the OWASP SQLi/XSS/CSRF kind, memory-unsafe C/C++, and Rust `unsafe` are NOT active threat surfaces for this project; they are catalogued below as inapplicable.

Each surface in the catalog below is evaluated against the project's
actual stack and code whenever the stack changes: surfaces that apply
are kept with project-specific rules; surfaces that do not apply are
recorded as inapplicable with a reason, so a later stack change
re-opens the question instead of silently missing it.

* Memory-unsafe code (C, C++, unsafe Rust blocks): INAPPLICABLE.
  Reason: the maintained runtime is pure Python; the legacy Rust
  implementation under `docs/legacy-rust/` is quarantined and not
  built. Reopens if any `unsafe { ... }` Rust block is reintroduced.

* Web/HTTP surface (routes, APIs, templates): INAPPLICABLE for public
  web. Reason: the maintained runtime does not serve user-facing
  HTTP routes; the embedded MCP HTTP transport is local-loopback only,
  bound to the configured host (default 127.0.0.1) via
  `ralph/mcp/server/_fallback_http_handler.py`. Reopens if a public
  web endpoint is added (parameterized queries, output encoding,
  CSRF tokens, SameSite cookies become mandatory at that point).

* Deserialization and parsing of external data: ACTIVE.
  - Allowed formats: JSON (stdlib `json`, rapidjson-like speeds via
    `orjson` if/when introduced), NDJSON streams from agent
    subprocesses, `tomllib` / `tomli_w` for policy TOML.
  - Forbidden: `pickle.loads` on any input that crosses a trust
    boundary; `yaml.load` without a safe loader (use `yaml.safe_load`
    or `yaml.CSafeLoader`); XML external-entity expansion (parse XML
    with `defusedxml`).
  - Boundary: subprocess NDJSON lines are parsed by stream parsers
    under `ralph/parsers/`; the parser boundary validates the JSON
    envelope shape before construction.

* Subprocess and shell execution: ACTIVE.
  - Argument-vector invocation only: `subprocess.run(args_list, ...)`
    with shell=False (the default); never `shell=True` with
    interpolated input.
  - Explicit executable paths where feasible; PATH resolution happens
    only at the trust boundary.
  - Every subprocess call under `ralph/mcp/`, `ralph/git/`,
    `ralph/process/`, `ralph/executor/`, `ralph/agents/`, and
    `ralph/pro_support/` carries a `timeout=`; the audit
    `ralph/testing/audit_mcp_timeout.py` is the binding enforcement.
  - The ONLY bypass for an unbounded-by-design subprocess call is an
    inline `# mcp-timeout-ok: <reason>` marker with the reason.

* Filesystem access: ACTIVE.
  - All file operations route through `ralph/files/` (the
    MemoryWorkspace / FsWorkspace seam); agents MUST NOT write
    outside the workspace root declared by the CLI.
  - Path traversal: workspace writes are normalized against the
    declared root via `FsWorkspace.write_file` / `append_file` /
    `edit_file` (see `ralph/files/operations.py`); the `audit_tool_exec`
    checks catch net-new bypasses.
  - Temp-file hygiene: `tempfile.NamedTemporaryFile` (or
    `mkstemp` / `mkdtemp`) only; never predictable names like
    `/tmp/<pid>` with world-writable permissions.
  - Every fs path that originates from an MCP call is revalidated
    against the workspace root before any side effect.

* Cryptography and randomness: ACTIVE.
  - Approved primitives: `secrets` for tokens, session ids, salts;
    `hashlib` (SHA-256 / SHA-512) for fingerprints; `cryptography` /
    `pyca` for symmetric / asymmetric where it lands.
  - Forbidden: `random.random` for any security-relevant value
    (tokens, session ids, nonces, salts); hand-rolled crypto; bare
    MD5 / SHA-1 for integrity.
  - Boundary: secret-bearing paths under `ralph/mcp/server/` and
    `ralph/agents/invoke/` are the only places cryptographic
    randomness currently lands; the audit is documented here so a
    new secret-bearing surface is checked automatically.

* AI/agent execution surface (prompts, tool calls, generated commands):
  ACTIVE.
  - Treat model-generated commands and file paths as untrusted input:
    confine writes to the declared `workspace_path`; reject writes
    outside the root.
  - No interpolation of untrusted text into shell commands (the
    "exec-as-string" path is forbidden by `tool_exec`).
  - The watchdog contract — `IdleWatchdog` +
    `PostExitWatchdog` (`ralph/agents/idle_watchdog/`) — owns every
    wall-clock decision; ad-hoc `time.sleep()` loops in
    `ralph/agents/invoke.py` are forbidden and detected by
    `ralph/testing/audit_di_seam.py`.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents
rely on them when enforcing this policy and MUST keep them current as
the project evolves. Facts marked (owner-supplied) come from the project
owner and MUST NOT be inferred from the repository alone.

RALPH-FACT: data_sensitivity (owner-supplied): sensitive by default — Ralph Workflow runs as a developer-local CLI under the user's own credentials (the agent shells out to user-installed CLIs that authenticate in their own context), and the workspace it operates on inherits whatever data lives on disk. The default is sensitive code + secrets may be present in the tree; the agent SHOULD avoid echoing file contents in logs unless the user explicitly asks. Owner confirmation is pending for any deployment that handles PII or PHI.
RALPH-FACT: deployment_exposure (owner-supplied): local developer machine by default; no public deployment. The MCP HTTP transport is local-loopback (`127.0.0.1`) and is the only socket the runtime binds; no inbound traffic crosses the trust boundary in the default configuration. Operator confirmation is pending for any deployment that exposes the embedded MCP server to a shared network.
RALPH-FACT: secrets_management_mechanism: secrets are NEVER read from the source tree, committed config, or test fixtures. The CLI reads from the environment (`os.environ`) and from the user-installed agent CLIs' own credential stores (Claude Code / Codex / OpenCode / Cursor / etc. each manage their own secrets). Test fixtures MUST NOT contain realistic credentials — placeholders are required (`...`, `***`, environment-variable references).
RALPH-FACT: secret_scan_command: `python3 scripts/fabrication_guard.py --level 1 <file>` for every public-facing markdown edit (the production pre-commit hook is level 1; level 2 / 3 are opt-in verification layers). For the Git history and the broader repository tree, `python3 scripts/verify_social_proof.py` runs the level-1 sweep as part of `make verify`; both `scripts/fabrication_guard.py` and `scripts/verify_social_proof.py` are wired through the social-proof verify step in `ralph/verify.py:_VERIFY_STEPS`. A blanket bypass of the pre-commit hook (`--no-verify`) is itself fabrication (per AGENTS.md § Non-negotiables).
RALPH-FACT: security_scanner_per_language: Python → `uv run bandit -q -r ralph/` (the language-scoped scanner called out in dependency-policy.md and Verification below). Other languages are recorded in the Verification section below as inapplicable.
RALPH-FACT: finding_waiver_convention: a security finding waiver carries the finding identifier (e.g. `[B105:hardcoded_password_string]` for Bandit), a per-file scope, a rationale, an owner, and a removal or review date. Waivers are recorded inline (via `# nosec: <id>` next to the suppressed line for Bandit) and again under "Exceptions" below with the same fields; the inline comment is not a substitute for the section entry. There is currently no Bandit `nosec` blanket allowlist in this project; introducing one requires an audit-style allowlist entry.

## AI execution instructions

To follow this policy, an agent making any change MUST:

* CHECK every change that touches a declared threat surface against
  that surface's rules before claiming the change complies, and say
  which surfaces the change touched.
* RECORD owner-supplied facts (data sensitivity, deployment exposure,
  threat model) from the project owner's input; never guess them. When
  owner input is unavailable, record the fail-safe conservative value
  labeled as an assumption pending owner confirmation, and raise the
  open confirmation at the next policy review.
* TREAT scanner findings by fixing the cause. A waiver requires the
  finding identifier, a rationale, an owner, and a review date under
  Exceptions — never a bare suppression comment.
* PREFER the ecosystem's established scanners (e.g. `bandit` or
  `semgrep` for Python, `gitleaks` or `detect-secrets` for secrets,
  `gosec` for Go, `cargo geiger` for unsafe-Rust auditing) over novel
  tooling; adding a new scanner requires a documented rationale.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (surfaces, facts, commands, requirements) in the
  same workflow that adds an input surface, a language, a subprocess or
  filesystem interaction, an auth decision, or a deployment target.

An agent MUST NOT:

* Commit, log, or print a secret — including realistic-looking test or
  placeholder credentials.
* Suppress or waive a scanner finding without an identifier, rationale,
  owner, and review date.
* Weaken, disable, or bypass any security control to obtain a passing
  result.
* Present an owner-unconfirmed threat model, data-sensitivity level, or
  exposure claim as verified, or substitute an optimistic assumption
  for the fail-safe conservative one.
* Interpolate untrusted input into shell commands, SQL strings, or
  file paths on any declared surface.

## Verification

Run every gate below before claiming a change complies with this policy.

Secret scanning applies to every project:

RALPH-COMMAND: python3 scripts/fabrication_guard.py --level 1 README.md ralph-workflow/README.md SHOWCASE.md USERS.md ralph-workflow/docs/sphinx/index.rst

This is the per-file level-1 sweep the pre-commit hook runs as
default; the level-2/3 existence checks are opt-in and require
network/GITHUB_TOKEN. Failures list the affected claim, the file,
and the verification target.

Per-language static security analysis follows this template (one block
per project language, or an explicit inapplicability record with a
reason):

RALPH-LANG: Python
RALPH-COMMAND: uv run bandit -q -r ralph/

RALPH-LANG: TypeScript
RALPH-INAPPLICABLE: reason - no TypeScript source exists in this project; reopens when the first `.ts` file lands - at which point `RALPH-COMMAND: npx semgrep --config p/typescript` becomes the gate.

RALPH-LANG: Rust
RALPH-INAPPLICABLE: reason - no active Rust source exists in this project (the legacy implementation is quarantined); reopens when active Rust code is reintroduced - at which point `RALPH-COMMAND: cargo geiger` (and `cargo clippy --all-targets` as a secondary check) becomes the gate.

RALPH-LANG: Go
RALPH-INAPPLICABLE: reason - no Go source exists in this project; reopens when the first Go file lands - at which point `RALPH-COMMAND: gosec ./...` becomes the gate.

RALPH-LANG: JavaScript
RALPH-INAPPLICABLE: reason - no JavaScript source carries a published security gate in this project (the ralph-workflow/skills-package/ artefact has zero runtime dependencies); reopens when JS source gains a Node-side surface - at which point `RALPH-COMMAND: npx semgrep --config p/javascript` becomes the gate.

RALPH-LANG: Ruby
RALPH-INAPPLICABLE: reason - the only Ruby file in the project is the Homebrew formula (a build-distribution artefact, syntax-checked via `make formula-check`); no Ruby security scanner is wired up. Reopens if a real Ruby source tree is added - at which point `RALPH-COMMAND: bundle exec brakeman` or `RALPH-COMMAND: bundle exec rubocop` becomes the gate.

The expected successful result is exit 0 with no unwaived findings. On
failure, report the finding identifier, the affected file, and the
category (secret, injection, unsafe API, unsafe deserialization,
subprocess, filesystem, crypto). Security scanners produce false
positives by design: triage every finding and either fix it or waive it
with a documented rationale under Exceptions — never silence it to
obtain green.

## Exceptions

A waived scanner finding or an accepted risk requires a documented
rationale, the finding identifier or risk description, the scope, the
owner, and a review or removal date. An inapplicable threat surface
requires a recorded reason that names the stack facts it depends on.
Exceptions expire at the next policy review; an expired exception
without an updated rationale is treated as a violation.

* Owner-unconfirmed facts: `data_sensitivity` and `deployment_exposure`
  are marked `(owner-supplied)` and run at the project-default
  conservative setting until the next maintainer review confirms
  the deployed scope (currently every release runs as
  sensitive-code-on-a-local-developer-machine; the broader
  deployment scope is unconfirmed at the time of writing and is
  listed here as an active open item).

## Maintenance triggers

Security posture decays faster than any other policy domain: new code
adds surfaces, new dependencies add exposure, and a threat model goes
stale silently. This policy MUST be reviewed in the same workflow as
any of:

* A new input surface is added (endpoint, CLI flag, file format,
  message consumer, environment variable, tool call).
* A new language, framework, or deployment target is introduced.
* Authentication, authorization, or session handling changes.
* A secret, key, or credential mechanism changes.
* A security scanner, its ruleset, or the secret-scan tool changes.
* A security incident, near miss, or newly disclosed vulnerability
  class relevant to a declared surface occurs.
* A stack change makes a previously inapplicable surface potentially
  applicable (e.g. a first HTTP endpoint, a first C extension).
* An owner-supplied fact (data sensitivity, deployment exposure) is
  confirmed or revised.

## Research basis

* publisher: OWASP Foundation
  title: "OWASP Top Ten"
  http: https://owasp.org/www-project-top-ten/
  review date: 2026-07-12

* publisher: OWASP Foundation
  title: "OWASP Application Security Verification Standard (ASVS)"
  http: https://owasp.org/www-project-application-security-verification-standard/
  review date: 2026-07-12

* publisher: OWASP Foundation
  title: "OWASP Cheat Sheet Series"
  http: https://cheatsheetseries.owasp.org/
  review date: 2026-07-12

* publisher: MITRE
  title: "CWE Top 25 Most Dangerous Software Weaknesses"
  http: https://cwe.mitre.org/top25/
  review date: 2026-07-12

* publisher: NIST
  title: "Secure Software Development Framework (SSDF)"
  http: https://csrc.nist.gov/projects/ssdf
  review date: 2026-07-12

* publisher: OpenSSF (Linux Foundation)
  title: "Concise Guide for Developing More Secure Software"
  http: https://best.openssf.org/Concise-Guide-for-Developing-More-Secure-Software
  review date: 2026-07-12

* publisher: MIT (Jerome H. Saltzer and Michael D. Schroeder)
  title: "The Protection of Information in Computer Systems"
  http: https://web.mit.edu/Saltzer/www/publications/protection/
  review date: 2026-07-12

## Living document contract

This policy is a living document — more so than any other in this
directory, because its substance is defined by the project's own threat
surfaces rather than by universal domain rules. It MUST evolve as the
project grows: update the surfaces, resolved facts, commands, and
requirements whenever verified project reality changes (new frameworks,
new commands, new structure). Two guardrails bound every amendment:

* Conflicts between this policy's generic defaults and the project's
  established practice are resolved in
  favor of the existing project policy — adapt this file to verified
  project reality, never the reverse. A looser project practice is
  NOT such a conflict: keep the stronger requirement unless a
  documented exception narrows it.
* An amendment MUST NOT subvert the INTENT of this policy. Weakening,
  disabling, or deleting a requirement so that a failing change passes is
  forbidden; evolution clarifies and extends, it does not water down.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: security-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
