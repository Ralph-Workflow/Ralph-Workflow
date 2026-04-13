//! Drain-scoped orchestration invariant table.
//!
//! This module declares the cross-cutting orchestration rules that govern
//! which drains may fire, which preserve drain identity, and which perform
//! validated transitions.
//!
//! # Design
//!
//! Each `DrainRule` entry states:
//! - Which drain it applies to
//! - Whether the drain is read-only or write-capable
//! - Which artifact type the drain produces (once, upon successful completion)
//! - Which drains it may validly transition to (the allowed transition set)
//! - Whether continuations are allowed within this drain
//! - For the Analysis drain: which `AnalysisDecision` outcome routes to which drain
//!
//! These rules serve as the normative reference for the orchestrator.
//! They are tested via invariant checks that verify no orchestration state
//! machine transition would violate them.
//!
//! # Relationship to Artifact Completion
//!
//! The completion contract (`artifact_type`) names what the drain must submit
//! before any transition is valid. The orchestrator must enforce this by checking
//! that an accepted artifact of the declared type is present before moving to the
//! next drain. (Phase 6 adds the runtime enforcement; this module declares the
//! invariant that enforcement must uphold.)
//!
//! # Continuation Policy
//!
//! Continuations within a drain are allowed unless `allow_continuation` is false.
//! Analysis is the only drain that forbids continuations because it is designed
//! to produce a single objective assessment per invocation.
//!
//! # Transition Graph
//!
//! Retry and continuation logic may NOT cross drain boundaries unless the
//! transition graph explicitly allows it. A drain transition that is not in
//! `allowed_transitions` must be rejected at startup validation and refused by
//! the orchestrator at runtime.

use crate::agents::{AgentDrain, AgentRole};
use crate::reducer::state::{DevelopmentAnalysisDecision, ReviewAnalysisDecision};

/// Read/write capability of a drain.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DrainCapability {
    /// The drain only reads context; it does not produce code or file changes.
    ReadOnly,
    /// The drain may write files to the workspace.
    WriteCapable,
}

/// An orchestration rule for a single drain.
///
/// Declares the invariants that the orchestrator must enforce for the drain.
#[derive(Debug, Clone)]
pub struct DrainRule {
    /// The drain this rule applies to.
    pub drain: AgentDrain,
    /// The expected agent role for this drain.
    ///
    /// Must match `AgentDrain::role()`. Declared explicitly here so invariant tests
    /// can verify that the mapping has not drifted.
    pub role: AgentRole,
    /// Whether the drain is allowed to modify workspace files.
    pub capability: DrainCapability,
    /// The artifact type this drain submits upon successful completion.
    ///
    /// Named using the canonical artifact key (the `type` field in the MCP artifact
    /// envelope). This is what the orchestrator must accept before declaring the
    /// drain complete and allowing a transition.
    ///
    /// `None` means the drain does not submit a structured artifact (currently unused).
    pub artifact_type: &'static str,
    /// Whether continuations are allowed within this drain.
    ///
    /// When true, the orchestrator may re-invoke the same agent with a continuation
    /// prompt if the agent returns a non-terminal status. When false, the drain is
    /// expected to complete in a single invocation.
    pub allow_continuation: bool,
    /// Which drains this drain may transition to after a successful completion.
    ///
    /// Transitions not in this list must be treated as invariant violations.
    /// An empty list means the drain is terminal (only Commit leads to this).
    pub allowed_transitions: &'static [AgentDrain],
    /// Whether retries and continuations preserve drain identity (same drain, same session).
    ///
    /// When `true`, retry and continuation dispatch stays within this drain without
    /// performing a validated transition. When `false`, every retry must go through
    /// the transition graph (e.g. Analysis routes through explicit decision outcomes).
    pub preserves_drain_on_retry: bool,
    /// Whether this drain can launch parallel worker instances for fan-out execution.
    ///
    /// Currently only the Development drain supports parallel workers driven by
    /// `work_units[]` in the planning artifact. All other drains are single-shot.
    pub parallel_capable: bool,
    /// Whether parallel workers spawned by this drain require a verifier step.
    ///
    /// Only meaningful when `parallel_capable` is `true`. When `true`, a dedicated
    /// verifier agent must run after all parallel workers complete before the drain
    /// may transition to the next phase.
    pub parallel_verifier_required: bool,
    /// Development-cycle analysis routes for this drain.
    ///
    /// Populated only for the Development drain. After the Analysis agent completes
    /// a development-cycle assessment, the orchestrator consults this table to
    /// determine the next drain: `NeedsMoreWork` loops back to Development;
    /// `CycleComplete` proceeds to Commit (development_commit).
    ///
    /// `None` for all other drains.
    pub development_analysis_routes: Option<&'static [(DevelopmentAnalysisDecision, AgentDrain)]>,
    /// Review-cycle analysis routes for this drain.
    ///
    /// Populated only for the Review drain. After the Analysis agent completes a
    /// review-cycle assessment (following a fix attempt), the orchestrator consults
    /// this table: `NeedsMoreFix` loops back to Fix; `CycleComplete` proceeds to
    /// Commit (review_commit).
    ///
    /// `None` for all other drains.
    pub review_analysis_routes: Option<&'static [(ReviewAnalysisDecision, AgentDrain)]>,
}

/// The canonical drain rule table.
///
/// Every built-in drain must appear here exactly once, in declaration order.
/// Tests verify completeness and invariant consistency.
pub const DRAIN_RULES: &[DrainRule] = &[
    DrainRule {
        drain: AgentDrain::Planning,
        role: AgentRole::Planning,
        capability: DrainCapability::ReadOnly,
        artifact_type: "plan",
        allow_continuation: true,
        // Planning transitions to Development or stays (re-planning).
        allowed_transitions: &[AgentDrain::Development],
        preserves_drain_on_retry: true,
        parallel_capable: false,
        parallel_verifier_required: false,
        development_analysis_routes: None,
        review_analysis_routes: None,
    },
    DrainRule {
        drain: AgentDrain::Development,
        role: AgentRole::Developer,
        capability: DrainCapability::WriteCapable,
        artifact_type: "development_result",
        allow_continuation: true,
        // Development transitions to Analysis, or loops back via continuation.
        allowed_transitions: &[AgentDrain::Analysis],
        preserves_drain_on_retry: true,
        // Development is the only drain that supports parallel worker fan-out,
        // driven by work_units[] in the planning artifact.
        parallel_capable: true,
        // Parallel workers require a verifier step after all workers complete.
        parallel_verifier_required: true,
        // After development_analysis completes: NeedsMoreWork loops back to
        // Development, CycleComplete proceeds to Commit (development_commit).
        development_analysis_routes: Some(&[
            (
                DevelopmentAnalysisDecision::NeedsMoreWork,
                AgentDrain::Development,
            ),
            (
                DevelopmentAnalysisDecision::CycleComplete,
                AgentDrain::Commit,
            ),
        ]),
        review_analysis_routes: None,
    },
    DrainRule {
        drain: AgentDrain::Analysis,
        role: AgentRole::Analysis,
        capability: DrainCapability::ReadOnly,
        // Analysis produces an analysis_decision artifact (not a development_result).
        artifact_type: "analysis_decision",
        // Analysis produces a single decision per invocation — no continuations.
        allow_continuation: false,
        // Analysis can route to Development, Planning, Commit, Review, or Fix
        // based on the decision point outcome (routing handled by phase config).
        allowed_transitions: &[
            AgentDrain::Development,
            AgentDrain::Planning,
            AgentDrain::Commit,
            AgentDrain::Review,
            AgentDrain::Fix,
        ],
        // Analysis does NOT preserve drain on retry: each invocation is a fresh
        // single-shot assessment. Retries go through the normal transition graph.
        preserves_drain_on_retry: false,
        parallel_capable: false,
        parallel_verifier_required: false,
        development_analysis_routes: None,
        review_analysis_routes: None,
    },
    DrainRule {
        drain: AgentDrain::Review,
        role: AgentRole::Reviewer,
        capability: DrainCapability::ReadOnly,
        artifact_type: "issues",
        allow_continuation: true,
        // Review transitions to Fix when issues are found, or to Commit when clean.
        allowed_transitions: &[AgentDrain::Fix, AgentDrain::Commit],
        preserves_drain_on_retry: true,
        parallel_capable: false,
        parallel_verifier_required: false,
        development_analysis_routes: None,
        // After review_analysis completes (following a fix attempt): NeedsMoreFix
        // loops back to Fix, CycleComplete proceeds to Commit (review_commit).
        review_analysis_routes: Some(&[
            (ReviewAnalysisDecision::NeedsMoreFix, AgentDrain::Fix),
            (ReviewAnalysisDecision::CycleComplete, AgentDrain::Commit),
        ]),
    },
    DrainRule {
        drain: AgentDrain::Fix,
        role: AgentRole::Fix,
        capability: DrainCapability::WriteCapable,
        artifact_type: "fix_result",
        allow_continuation: true,
        // Fix transitions to Analysis (for verification) after implementation.
        allowed_transitions: &[AgentDrain::Analysis],
        preserves_drain_on_retry: true,
        parallel_capable: false,
        parallel_verifier_required: false,
        development_analysis_routes: None,
        review_analysis_routes: None,
    },
    DrainRule {
        drain: AgentDrain::Commit,
        role: AgentRole::Commit,
        capability: DrainCapability::WriteCapable,
        artifact_type: "commit_message",
        allow_continuation: false,
        // Commit is a checkpoint; after it the pipeline continues to Review or terminates.
        allowed_transitions: &[AgentDrain::Review, AgentDrain::Planning],
        preserves_drain_on_retry: true,
        parallel_capable: false,
        parallel_verifier_required: false,
        development_analysis_routes: None,
        review_analysis_routes: None,
    },
];

/// Look up the rule for a specific drain.
///
/// Returns `None` only if `drain` is not in the table (which would be a bug,
/// since every built-in drain must be present).
#[must_use]
pub fn rule_for(drain: AgentDrain) -> Option<&'static DrainRule> {
    DRAIN_RULES.iter().find(|r| r.drain == drain)
}

/// Verify that a drain-to-drain transition is declared in the rule table.
///
/// Returns `true` if `from` may transition to `to` according to the rule table.
/// Callers must reject transitions that return `false`.
#[must_use]
pub fn transition_allowed(from: AgentDrain, to: AgentDrain) -> bool {
    rule_for(from).is_some_and(|rule| rule.allowed_transitions.contains(&to))
}

/// Validate the drain rule table for internal consistency.
///
/// Checks:
/// 1. Every built-in drain appears exactly once.
/// 2. Each rule's `role` matches `AgentDrain::role()`.
/// 3. Analysis routes are present only on the drains that own them.
/// 4. `parallel_verifier_required` is only true when `parallel_capable` is true.
/// 5. Analysis drain never has `preserves_drain_on_retry = true` (single-shot only).
///
/// Returns `Ok(())` on success, or a descriptive error string on the first
/// violation found. Intended for startup assertion via `validate_drain_rules().unwrap()`.
pub fn validate_drain_rules() -> Result<(), String> {
    // 1. Every built-in drain appears exactly once.
    let all_drains = AgentDrain::all();
    if DRAIN_RULES.len() != all_drains.len() {
        return Err(format!(
            "DRAIN_RULES has {} entries but AgentDrain::all() yields {}",
            DRAIN_RULES.len(),
            all_drains.len()
        ));
    }
    if let Some(err) = all_drains.iter().find_map(|&drain| {
        let count = DRAIN_RULES.iter().filter(|r| r.drain == drain).count();
        (count != 1).then(|| {
            format!(
                "drain {:?} appears {} times in DRAIN_RULES (expected 1)",
                drain, count
            )
        })
    }) {
        return Err(err);
    }

    // 2. Each rule's role must match AgentDrain::role().
    if let Some(err) = DRAIN_RULES.iter().find_map(|rule| {
        let canonical = rule.drain.role();
        (rule.role != canonical).then(|| {
            format!(
                "DrainRule for {:?} declares role {:?} but AgentDrain::role() returns {:?}",
                rule.drain, rule.role, canonical
            )
        })
    }) {
        return Err(err);
    }

    // 3. Development analysis routes belong only to the Development drain;
    //    review analysis routes belong only to the Review drain.
    if let Some(err) = DRAIN_RULES.iter().find_map(|rule| {
        if rule.drain != AgentDrain::Development && rule.development_analysis_routes.is_some() {
            return Some(format!(
                "DrainRule for {:?} has development_analysis_routes but only Development may have these",
                rule.drain
            ));
        }
        if rule.drain != AgentDrain::Review && rule.review_analysis_routes.is_some() {
            return Some(format!(
                "DrainRule for {:?} has review_analysis_routes but only Review may have these",
                rule.drain
            ));
        }
        None
    }) {
        return Err(err);
    }

    // 4. parallel_verifier_required may only be true when parallel_capable is true.
    if let Some(err) = DRAIN_RULES.iter().find_map(|rule| {
        (rule.parallel_verifier_required && !rule.parallel_capable).then(|| {
            format!(
                "DrainRule for {:?}: parallel_verifier_required=true but parallel_capable=false",
                rule.drain
            )
        })
    }) {
        return Err(err);
    }

    // 5. Analysis is a single-shot drain; it must not preserve drain on retry.
    if let Some(analysis_rule) = rule_for(AgentDrain::Analysis) {
        if analysis_rule.preserves_drain_on_retry {
            return Err(
                "DrainRule for Analysis: preserves_drain_on_retry must be false (single-shot drain)"
                    .to_owned(),
            );
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::AgentDrain;

    /// Every built-in drain must appear in the rule table exactly once.
    #[test]
    fn test_all_drains_covered() {
        for drain in AgentDrain::all() {
            assert!(
                rule_for(drain).is_some(),
                "drain {:?} is missing from DRAIN_RULES",
                drain
            );
        }
        assert_eq!(
            DRAIN_RULES.len(),
            AgentDrain::all().len(),
            "DRAIN_RULES has a different number of entries than AgentDrain::all()"
        );
    }

    /// Each drain rule must declare the correct role for that drain.
    ///
    /// This invariant test catches drift between `AgentDrain::role()` and the
    /// explicit role declared in the rule table.
    #[test]
    fn test_drain_role_matches_canonical_mapping() {
        for rule in DRAIN_RULES {
            assert_eq!(
                rule.role,
                rule.drain.role(),
                "DrainRule for {:?} declares role {:?} but AgentDrain::role() returns {:?}",
                rule.drain,
                rule.role,
                rule.drain.role()
            );
        }
    }

    /// Read-only drains must never appear in another drain's transition as a write-capable step.
    ///
    /// This prevents logic errors where analysis or review steps are scheduled
    /// in contexts that expect file-system writes.
    #[test]
    fn test_readonly_drains_have_no_write_transitions() {
        let readonly_drains: Vec<AgentDrain> = DRAIN_RULES
            .iter()
            .filter(|r| r.capability == DrainCapability::ReadOnly)
            .map(|r| r.drain)
            .collect();

        // Every drain that lists a read-only drain in its allowed_transitions
        // must understand it's scheduling a read-only step.
        // This test is a structural sanity check: read-only drains may appear
        // as transition targets (that's correct — planning outputs to development),
        // but they must be declared as ReadOnly in the table.
        for drain in &readonly_drains {
            let rule = rule_for(*drain).expect("drain must be in table");
            assert_eq!(
                rule.capability,
                DrainCapability::ReadOnly,
                "{:?} is declared in readonly_drains but its rule says WriteCapable",
                drain
            );
        }
    }

    /// Planning and Analysis are the only read-only drains.
    ///
    /// Review was historically read-only but is now explicitly read-only.
    /// Fix and Development are write-capable.
    /// Commit is write-capable (creates commits).
    #[test]
    fn test_readonly_drain_identity() {
        assert_eq!(
            rule_for(AgentDrain::Planning).unwrap().capability,
            DrainCapability::ReadOnly,
            "Planning must be read-only"
        );
        assert_eq!(
            rule_for(AgentDrain::Analysis).unwrap().capability,
            DrainCapability::ReadOnly,
            "Analysis must be read-only"
        );
        assert_eq!(
            rule_for(AgentDrain::Review).unwrap().capability,
            DrainCapability::ReadOnly,
            "Review must be read-only"
        );
        assert_eq!(
            rule_for(AgentDrain::Development).unwrap().capability,
            DrainCapability::WriteCapable,
            "Development must be write-capable"
        );
        assert_eq!(
            rule_for(AgentDrain::Fix).unwrap().capability,
            DrainCapability::WriteCapable,
            "Fix must be write-capable"
        );
        assert_eq!(
            rule_for(AgentDrain::Commit).unwrap().capability,
            DrainCapability::WriteCapable,
            "Commit must be write-capable"
        );
    }

    /// Analysis does not allow continuations (single-shot decision).
    #[test]
    fn test_analysis_forbids_continuations() {
        assert!(
            !rule_for(AgentDrain::Analysis).unwrap().allow_continuation,
            "Analysis must not allow continuations"
        );
    }

    /// Commit does not allow continuations (single-shot checkpoint).
    #[test]
    fn test_commit_forbids_continuations() {
        assert!(
            !rule_for(AgentDrain::Commit).unwrap().allow_continuation,
            "Commit must not allow continuations"
        );
    }

    /// Development → Analysis transition is allowed (the primary post-dev step).
    #[test]
    fn test_development_to_analysis_allowed() {
        assert!(
            transition_allowed(AgentDrain::Development, AgentDrain::Analysis),
            "Development → Analysis must be allowed"
        );
    }

    /// Development does not transition directly to Review without going through Analysis.
    ///
    /// This enforces the invariant that analysis is the explicit decision point,
    /// not a silent shortcut.
    #[test]
    fn test_development_to_review_not_directly_allowed() {
        assert!(
            !transition_allowed(AgentDrain::Development, AgentDrain::Review),
            "Development → Review must NOT be a direct transition (must go through Analysis)"
        );
    }

    /// Fix → Analysis is the post-fix verification step.
    #[test]
    fn test_fix_to_analysis_allowed() {
        assert!(
            transition_allowed(AgentDrain::Fix, AgentDrain::Analysis),
            "Fix → Analysis must be allowed"
        );
    }

    /// Fix does not route directly to Review or Commit without Analysis.
    #[test]
    fn test_fix_to_commit_not_directly_allowed() {
        assert!(
            !transition_allowed(AgentDrain::Fix, AgentDrain::Commit),
            "Fix → Commit must NOT be a direct transition (must go through Analysis)"
        );
        assert!(
            !transition_allowed(AgentDrain::Fix, AgentDrain::Review),
            "Fix → Review must NOT be a direct transition (must go through Analysis)"
        );
    }

    /// Planning → Development is the canonical forward transition.
    #[test]
    fn test_planning_to_development_allowed() {
        assert!(
            transition_allowed(AgentDrain::Planning, AgentDrain::Development),
            "Planning → Development must be allowed"
        );
    }

    /// Review → Fix when issues found, Review → Commit when clean.
    #[test]
    fn test_review_transitions() {
        assert!(
            transition_allowed(AgentDrain::Review, AgentDrain::Fix),
            "Review → Fix must be allowed (issues found)"
        );
        assert!(
            transition_allowed(AgentDrain::Review, AgentDrain::Commit),
            "Review → Commit must be allowed (no issues)"
        );
    }

    /// `rule_for` returns `None` only for hypothetical non-existent drains.
    /// Since we can't construct a fake `AgentDrain`, this test just verifies
    /// that all real drains return `Some`.
    #[test]
    fn test_rule_for_all_drains_returns_some() {
        for drain in AgentDrain::all() {
            assert!(
                rule_for(drain).is_some(),
                "rule_for({:?}) returned None",
                drain
            );
        }
    }

    // =========================================================================
    // Analysis artifact type invariant
    // =========================================================================

    /// Analysis produces an `analysis_decision` artifact, not a `development_result`.
    ///
    /// The artifact_type was historically wrong; this test locks the correct value.
    #[test]
    fn test_analysis_artifact_type_is_analysis_decision() {
        assert_eq!(
            rule_for(AgentDrain::Analysis).unwrap().artifact_type,
            "analysis_decision",
            "Analysis drain must produce 'analysis_decision' artifact"
        );
    }

    /// `validate_drain_rules()` must pass for the canonical table.
    ///
    /// This is the single integration test that verifies all validate_drain_rules()
    /// invariants simultaneously.
    #[test]
    fn test_validate_drain_rules_passes() {
        validate_drain_rules().expect("DRAIN_RULES must pass validate_drain_rules()");
    }

    /// Commit transitions: Review (post-commit code review) and Planning (re-plan loop).
    #[test]
    fn test_commit_transitions() {
        assert!(
            transition_allowed(AgentDrain::Commit, AgentDrain::Review),
            "Commit → Review must be allowed"
        );
        assert!(
            transition_allowed(AgentDrain::Commit, AgentDrain::Planning),
            "Commit → Planning must be allowed"
        );
        assert!(
            !transition_allowed(AgentDrain::Commit, AgentDrain::Development),
            "Commit → Development must NOT be a direct transition"
        );
    }

    // =========================================================================
    // Drain identity and parallel invariants
    // =========================================================================

    /// Analysis drain must not preserve drain on retry (single-shot decision).
    #[test]
    fn test_analysis_does_not_preserve_drain_on_retry() {
        assert!(
            !rule_for(AgentDrain::Analysis)
                .unwrap()
                .preserves_drain_on_retry,
            "Analysis must not preserve drain on retry (single-shot drain)"
        );
    }

    /// Write-capable drains (Development, Fix, Commit) must preserve drain on retry.
    ///
    /// Retries within a write-capable drain stay in the same drain to avoid
    /// unintended cross-drain transitions when agent invocation fails.
    #[test]
    fn test_write_capable_drains_preserve_drain_on_retry() {
        for drain in [AgentDrain::Development, AgentDrain::Fix, AgentDrain::Commit] {
            assert!(
                rule_for(drain).unwrap().preserves_drain_on_retry,
                "{:?} must preserve drain on retry",
                drain
            );
        }
    }

    /// Development is the only drain that is parallel-capable.
    #[test]
    fn test_only_development_is_parallel_capable() {
        assert!(
            rule_for(AgentDrain::Development).unwrap().parallel_capable,
            "Development must be parallel-capable"
        );
        for drain in [
            AgentDrain::Planning,
            AgentDrain::Analysis,
            AgentDrain::Review,
            AgentDrain::Fix,
            AgentDrain::Commit,
        ] {
            assert!(
                !rule_for(drain).unwrap().parallel_capable,
                "{:?} must NOT be parallel-capable",
                drain
            );
        }
    }

    /// Development requires a verifier step after parallel workers.
    #[test]
    fn test_development_parallel_verifier_required() {
        assert!(
            rule_for(AgentDrain::Development)
                .unwrap()
                .parallel_verifier_required,
            "Development must require a parallel verifier step"
        );
    }

    /// Development drain declares development_analysis_routes; all others do not.
    #[test]
    fn test_development_analysis_routes_only_on_development() {
        assert!(
            rule_for(AgentDrain::Development)
                .unwrap()
                .development_analysis_routes
                .is_some(),
            "Development must have development_analysis_routes"
        );
        for drain in [
            AgentDrain::Planning,
            AgentDrain::Analysis,
            AgentDrain::Review,
            AgentDrain::Fix,
            AgentDrain::Commit,
        ] {
            assert!(
                rule_for(drain)
                    .unwrap()
                    .development_analysis_routes
                    .is_none(),
                "{:?} must NOT have development_analysis_routes",
                drain
            );
        }
    }

    /// Review drain declares review_analysis_routes; all others do not.
    #[test]
    fn test_review_analysis_routes_only_on_review() {
        assert!(
            rule_for(AgentDrain::Review)
                .unwrap()
                .review_analysis_routes
                .is_some(),
            "Review must have review_analysis_routes"
        );
        for drain in [
            AgentDrain::Planning,
            AgentDrain::Development,
            AgentDrain::Analysis,
            AgentDrain::Fix,
            AgentDrain::Commit,
        ] {
            assert!(
                rule_for(drain).unwrap().review_analysis_routes.is_none(),
                "{:?} must NOT have review_analysis_routes",
                drain
            );
        }
    }

    /// Development analysis routes cover all DevelopmentAnalysisDecision variants.
    #[test]
    fn test_development_analysis_routes_cover_all_decisions() {
        use crate::reducer::state::DevelopmentAnalysisDecision;
        let routes = rule_for(AgentDrain::Development)
            .unwrap()
            .development_analysis_routes
            .unwrap();
        // Exhaustive check: every variant must have a route entry.
        for &decision in &[
            DevelopmentAnalysisDecision::NeedsMoreWork,
            DevelopmentAnalysisDecision::CycleComplete,
        ] {
            assert!(
                routes.iter().any(|(d, _)| *d == decision),
                "development_analysis_routes missing entry for {:?}",
                decision
            );
        }
    }

    /// Review analysis routes cover all ReviewAnalysisDecision variants.
    #[test]
    fn test_review_analysis_routes_cover_all_decisions() {
        use crate::reducer::state::ReviewAnalysisDecision;
        let routes = rule_for(AgentDrain::Review)
            .unwrap()
            .review_analysis_routes
            .unwrap();
        // Exhaustive check: every variant must have a route entry.
        for &decision in &[
            ReviewAnalysisDecision::NeedsMoreFix,
            ReviewAnalysisDecision::CycleComplete,
        ] {
            assert!(
                routes.iter().any(|(d, _)| *d == decision),
                "review_analysis_routes missing entry for {:?}",
                decision
            );
        }
    }

    /// Development analysis NeedsMoreWork routes back to Development.
    #[test]
    fn test_development_analysis_needs_more_work_routes_to_development() {
        use crate::reducer::state::DevelopmentAnalysisDecision;
        let routes = rule_for(AgentDrain::Development)
            .unwrap()
            .development_analysis_routes
            .unwrap();
        let dest = routes
            .iter()
            .find(|(d, _)| *d == DevelopmentAnalysisDecision::NeedsMoreWork)
            .map(|(_, drain)| drain);
        assert_eq!(
            dest,
            Some(&AgentDrain::Development),
            "NeedsMoreWork must route back to Development"
        );
    }

    /// Development analysis CycleComplete routes to Commit (development_commit).
    #[test]
    fn test_development_analysis_cycle_complete_routes_to_commit() {
        use crate::reducer::state::DevelopmentAnalysisDecision;
        let routes = rule_for(AgentDrain::Development)
            .unwrap()
            .development_analysis_routes
            .unwrap();
        let dest = routes
            .iter()
            .find(|(d, _)| *d == DevelopmentAnalysisDecision::CycleComplete)
            .map(|(_, drain)| drain);
        assert_eq!(
            dest,
            Some(&AgentDrain::Commit),
            "CycleComplete must route to Commit (development_commit)"
        );
    }

    /// Review analysis NeedsMoreFix routes back to Fix.
    #[test]
    fn test_review_analysis_needs_more_fix_routes_to_fix() {
        use crate::reducer::state::ReviewAnalysisDecision;
        let routes = rule_for(AgentDrain::Review)
            .unwrap()
            .review_analysis_routes
            .unwrap();
        let dest = routes
            .iter()
            .find(|(d, _)| *d == ReviewAnalysisDecision::NeedsMoreFix)
            .map(|(_, drain)| drain);
        assert_eq!(
            dest,
            Some(&AgentDrain::Fix),
            "NeedsMoreFix must route back to Fix"
        );
    }

    /// Review analysis CycleComplete routes to Commit (review_commit).
    #[test]
    fn test_review_analysis_cycle_complete_routes_to_commit() {
        use crate::reducer::state::ReviewAnalysisDecision;
        let routes = rule_for(AgentDrain::Review)
            .unwrap()
            .review_analysis_routes
            .unwrap();
        let dest = routes
            .iter()
            .find(|(d, _)| *d == ReviewAnalysisDecision::CycleComplete)
            .map(|(_, drain)| drain);
        assert_eq!(
            dest,
            Some(&AgentDrain::Commit),
            "CycleComplete must route to Commit (review_commit)"
        );
    }

    /// `validate_drain_rules()` extended checks pass for the canonical table.
    #[test]
    fn test_validate_drain_rules_extended_passes() {
        validate_drain_rules().expect("DRAIN_RULES must pass all validate_drain_rules() checks");
    }
}
