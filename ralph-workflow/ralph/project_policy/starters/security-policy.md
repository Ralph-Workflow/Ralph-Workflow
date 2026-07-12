<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: security-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

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

<!-- REPLACE-ME: evaluate every surface in the catalog below against the
project's actual stack and code. Record the active surfaces in the fact
line (each with concrete rules kept in this section), and record every
inactive surface as inapplicable with the stack change that would
re-open it. Keep the catalog itself: it is the checklist future agents
re-run whenever the stack changes. Then delete this comment. -->

RALPH-FACT: active_threat_surfaces: PROJECT-FACT-UNRESOLVED

Each surface in the catalog below is evaluated against the project's
actual stack and code whenever the stack changes: surfaces that apply
are kept with project-specific rules; surfaces that do not apply are
recorded as inapplicable with a reason, so a later stack change
re-opens the question instead of silently missing it.

* Memory-unsafe code (C, C++, unsafe Rust blocks): banned functions
  (`strcpy`, `strcat`, `sprintf`, `gets`, unbounded `scanf`), mandatory
  bounds-checked alternatives, sanitizer or analyzer gates (ASan/UBSan,
  compiler hardening flags), rules for every `unsafe` block.
* Web/HTTP surface (routes, APIs, templates): injection defenses
  (parameterized queries, no string-built SQL), output encoding and
  content security policy (XSS), request-forgery defenses (CSRF tokens,
  SameSite cookies), authentication, session, and authorization rules,
  transport security expectations.
* Deserialization and parsing of external data: allowed formats and
  parsers, bans on unsafe deserializers (`pickle` on untrusted data,
  `yaml.load` without a safe loader, XML external-entity expansion).
* Subprocess and shell execution: argument-vector invocation only,
  never shell-string interpolation of untrusted input, explicit
  executable paths where feasible.
* Filesystem access: path-traversal defenses on externally influenced
  paths, temp-file hygiene (no predictable names, correct permissions),
  no world-writable artifacts.
* Cryptography and randomness: approved libraries and primitives only,
  no hand-rolled crypto, cryptographically secure randomness for any
  security-relevant value (tokens, session ids, salts).
* AI/agent execution surface (prompts, tool calls, generated commands):
  treat model-generated commands and file paths as untrusted input,
  confine writes to declared workspace roots, no interpolation of
  untrusted text into shell commands.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves. Facts marked (owner-supplied) come from the project
owner and MUST NOT be inferred from the repository alone.

<!-- REPLACE-ME: record one verified, machine-checkable value per fact
below (commands, paths, names, versions — not adjectives or aspirations).
If the project is too young for a fact to be settled, record the best
current answer plus the condition that will settle it, e.g.
"none yet (assumed <date>; revisit when <trigger>)" — a future agent must
be able to tell a settled fact from a provisional one at a glance. Then
delete this comment. -->

RALPH-FACT: data_sensitivity (owner-supplied): PROJECT-FACT-UNRESOLVED
RALPH-FACT: deployment_exposure (owner-supplied): PROJECT-FACT-UNRESOLVED
RALPH-FACT: secrets_management_mechanism: PROJECT-FACT-UNRESOLVED
RALPH-FACT: secret_scan_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: security_scanner_per_language: PROJECT-FACT-UNRESOLVED
RALPH-FACT: finding_waiver_convention: PROJECT-FACT-UNRESOLVED

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

<!-- REPLACE-ME: set the project's real secret-scan command (e.g. gitleaks
or detect-secrets; the first token must be an approved gate tool — wrap
anything else in `make`, `uv run`, or `npx`). Do not declare a hollow
command. Then delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

Per-language static security analysis follows this template (one block
per project language, or an explicit inapplicability record with a
reason):

<!-- REPLACE-ME: per-language template. Keep one block per project language
with the real scanner command (first token must be an approved gate tool;
wrap others in `make`, `uv run`, or `npx`), add blocks for detected
languages missing below, drop blocks for languages the project does not
use, and record genuinely unscanned languages as inapplicable with a
reason. Then delete this comment. -->

RALPH-LANG: Python
RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

RALPH-LANG: TypeScript
RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

RALPH-LANG: Rust
RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

RALPH-LANG: Go
RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

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
