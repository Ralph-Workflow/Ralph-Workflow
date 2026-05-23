# Escalation audit: repeated issues not escalating like a human owner

## Severity-ranked findings

### 1. Critical — Escalation is side-effect-only and never changes the issue state machine
- **Mechanism:** `health_monitor.py` emits `escalation_required` records after `repeats >= 2`, but those records do not alter severity, ownership, suppression, retry policy, or routing for the underlying issue. The same base issue keeps reappearing while the monitor merely appends more escalation artifacts and reruns the same owner jobs.
- **Source layer:** Hidden repair loops / persistence.
- **Root cause:** Escalation is modeled as another log entry, not as a first-class incident with lifecycle state.
- **Evidence:** `agents/system/health_monitor.py:140-180`, `agents/system/health_monitor.py:866-869`; `agents/system/logs/health_monitor_latest.json` shows `agent_architecture_verifier` repeated 47 prior runs and `marketing_independent_verification` 37 prior runs despite owner-loop escalations being marked `ok`.
- **Impact:** The system can “escalate” dozens of times without any stronger response than before; this is exactly non-human behavior.
- **Concrete fix:** Introduce an incident registry keyed by `(name, category)` with fields like `first_seen`, `last_seen`, `repeat_count`, `escalation_level`, `owner`, `last_owner_action`, `next_action_due`, `blocked_by`, and `closed_at`. Make `repeat_issue_escalations()` transition incident state (warn → owner_page → human_page / global_block) instead of appending duplicate issues.

### 2. Critical — Architecture health is hard-blocked by unrelated subsystem failures, creating escalation deadlock
- **Mechanism:** `agent_architecture_independent_verify.py` and `agent_architecture_verifier.py` fail whenever `non_self_referential_health_issues()` returns any unrelated live issue. Right now the architecture verifier fails because marketing verification fails, and that architecture failure itself becomes a repeated escalated issue.
- **Source layer:** Tool interpretation / hidden cross-loop coupling.
- **Root cause:** The architecture signoff contract is global-health coupled rather than scoped to architecture-owned evidence.
- **Evidence:** `agents/system/agent_architecture_independent_verify.py:191-197`, `agents/system/agent_architecture_verifier.py:188-192`; `agents/system/logs/agent_architecture_independent_verification.json` lists blockers `marketing_independent_verification:loop_verification_fail`; `health_monitor_latest.json` then escalates both marketing and architecture verifier failures.
- **Impact:** One unhealthy subsystem poisons another subsystem’s certification, causing self-reinforcing repeat escalations with no path to closure.
- **Concrete fix:** Split verifier outcomes into `scope_verdict` and `global_readiness_verdict`. The architecture verifier should only fail closed on architecture-owned blockers; unrelated subsystem issues should be attached as watchpoints/dependencies, not converted into architecture failure unless an explicit dependency map says they are release-blocking.

### 3. High — Owner escalation has no progress test, no timeout to next level, and no human handoff threshold
- **Mechanism:** `apply_safe_repairs()` reruns owner jobs for `escalation_required`, counts success if the job enqueues or is already running, then stops. There is no check that the owner changed evidence, reduced repeat count, or resolved the incident.
- **Source layer:** Tool execution / hidden repair loop.
- **Root cause:** Success is defined as “job was triggered,” not “situation improved.”
- **Evidence:** `agents/system/health_monitor.py:547-625`; `health_monitor_latest.json` shows `owner_loop_escalation` entries marked `ok: true` even while repeated issues remain unresolved at 37–47 prior runs.
- **Impact:** The system confuses activity with ownership. A human owner would escalate further after many no-progress retries.
- **Concrete fix:** After every owner escalation, compare before/after incident state. If `repeat_count` or blocker set does not improve after N owner actions or T elapsed hours, automatically raise `escalation_level` to `human_required`, emit a dedicated page/alert artifact, and stop counting mere enqueue success as repair success.

### 4. High — Repeat detection is crude count-based and ignores recency, continuity, and causality
- **Mechanism:** `repeat_issue_escalations()` counts prior matching `(name, category)` entries across the last 50 history rows and escalates whenever count is at least 2.
- **Source layer:** Persistence.
- **Root cause:** The system has no notion of consecutive failures, unresolved windows, cooldowns, or “same root cause still active.”
- **Evidence:** `agents/system/health_monitor.py:145-176`.
- **Impact:** It can both under-react and over-react: old intermittent incidents count forever within the window, while truly urgent currently-consecutive failures get the same treatment as stale repeats.
- **Concrete fix:** Replace raw count logic with incident-window logic: escalate on `consecutive_occurrences`, `time_open`, and `failed_owner_attempts`. Store causal fingerprint(s) from `likely_cause`/blockers so the monitor can distinguish a recurring identical fault from a new one with the same name.

### 5. Medium — Docs loop learned a stronger stability model, but health escalation did not reuse that pattern
- **Mechanism:** `docs_loop_stability.py` and `ralph_docs_verify.py` explicitly track recent failures, consecutive clean passes, no-progress stops, and remediation pass exhaustion. The broader health escalation path does not adopt those concepts for non-doc incidents.
- **Source layer:** Architecture inconsistency across loops.
- **Root cause:** Reliability heuristics exist locally in the docs verifier but were not abstracted into a shared escalation policy.
- **Evidence:** `agents/system/docs_loop_stability.py:74-153`, `agents/docs_quality/ralph_docs_verify.py:173-223` versus the simpler escalation flow in `health_monitor.py`.
- **Impact:** One loop behaves more like a human owner than the global escalation system does.
- **Concrete fix:** Extract a shared `IncidentStabilityPolicy` used by health, docs, architecture, and marketing verifiers. Reuse fields like `recent_failures`, `consecutive_passes_since_last_fail`, `no_progress_failures`, and `stop_reason` to decide when to reopen, escalate, or require human review.

## Architecture diagnosis
The core failure is not that the system lacks escalation code; it lacks an **incident ownership state machine**. Repeated issues move through logging, reruns, and owner-job enqueueing, but never through a stronger contract like “someone owns this until resolved, and if retries don’t help, the system must change behavior.” Cross-loop coupling makes this worse: marketing failure invalidates architecture verification, which manufactures more architecture incidents, which then trigger more owner-loop activity without closure. In the 12-layer model, the break is primarily at **hidden repair loops, persistence, and tool interpretation**.

## Ordered fix plan
1. **Create first-class incident state** — Persist incident records separately from raw health snapshots and make escalation mutate incident state, not append duplicate issue rows.
2. **Add no-progress escalation policy** — Track owner attempts, evidence deltas, open duration, and consecutive unresolved runs; after threshold, require human/manual escalation.
3. **Decouple verifier scopes** — Make architecture, marketing, and docs verifiers fail on owned blockers only, while recording external dependencies as watchpoints.
4. **Adopt shared stability heuristics** — Promote the docs loop’s `consecutive_passes` / `no_progress` logic into a reusable escalation library.
5. **Change repair success criteria** — In `apply_safe_repairs()`, only mark escalation repair successful if the incident state improves after the owner action.

## Code-level implementation sketch
- Add `agents/system/incidents.py` with `load_incidents()`, `upsert_incident(issue)`, `advance_escalation(incident, observation)`, and `close_incident(key)`.
- Replace `repeat_issue_escalations()` with logic that returns state transitions such as `owner_retry`, `owner_failed_no_progress`, `human_required`, `dependency_blocked`.
- Update `apply_safe_repairs()` to write `last_owner_action_at`, `last_owner_action_result`, and `progress_delta` back into the incident store.
- Refactor `non_self_referential_health_issues()` consumers so they filter by dependency policy instead of “any unrelated issue means fail.”
- Add a top-level artifact like `agents/system/logs/open_incidents_latest.json` summarizing incidents by severity, owner, age, and next required action.
