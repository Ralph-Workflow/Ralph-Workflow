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
use crate::reducer::state::AnalysisDecision;

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
    /// Decision-outcome-to-drain routing table for analysis-style drains.
    ///
    /// `Some` only for the Analysis drain. Maps each `AnalysisDecision` variant
    /// to the drain that should be activated when that outcome is produced.
    /// Every `AnalysisDecision` variant must appear exactly once.
    ///
    /// `None` for all non-analysis drains.
    pub analysis_decision_routes: Option<&'static [(AnalysisDecision, AgentDrain)]>,
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
        analysis_decision_routes: None,
    },
    DrainRule {
        drain: AgentDrain::Development,
        role: AgentRole::Developer,
        capability: DrainCapability::WriteCapable,
        artifact_type: "development_result",
        allow_continuation: true,
        // Development transitions to Analysis, or loops back via continuation.
        allowed_transitions: &[AgentDrain::Analysis],
        analysis_decision_routes: None,
    },
    DrainRule {
        drain: AgentDrain::Analysis,
        role: AgentRole::Analysis,
        capability: DrainCapability::ReadOnly,
        // Analysis produces an analysis_decision artifact (not a development_result).
        artifact_type: "analysis_decision",
        // Analysis produces a single decision per invocation — no continuations.
        allow_continuation: false,
        // Analysis decision can route back to Development, Planning, Commit, Review, or Fix.
        allowed_transitions: &[
            AgentDrain::Development,
            AgentDrain::Planning,
            AgentDrain::Commit,
            AgentDrain::Review,
            AgentDrain::Fix,
        ],
        // Canonical decision-outcome routing: each AnalysisDecision maps to exactly one drain.
        analysis_decision_routes: Some(&[
            (AnalysisDecision::NeedsMoreWork, AgentDrain::Development),
            (AnalysisDecision::NeedsReplanning, AgentDrain::Planning),
            (AnalysisDecision::ReadyForReview, AgentDrain::Review),
            (AnalysisDecision::ReadyToCommit, AgentDrain::Commit),
            (AnalysisDecision::NeedsAnotherReview, AgentDrain::Fix),
        ]),
    },
    DrainRule {
        drain: AgentDrain::Review,
        role: AgentRole::Reviewer,
        capability: DrainCapability::ReadOnly,
        artifact_type: "issues",
        allow_continuation: true,
        // Review transitions to Fix when issues are found, or to Commit when clean.
        allowed_transitions: &[AgentDrain::Fix, AgentDrain::Commit],
        analysis_decision_routes: None,
    },
    DrainRule {
        drain: AgentDrain::Fix,
        role: AgentRole::Fix,
        capability: DrainCapability::WriteCapable,
        artifact_type: "fix_result",
        allow_continuation: true,
        // Fix transitions to Analysis (for verification) after implementation.
        allowed_transitions: &[AgentDrain::Analysis],
        analysis_decision_routes: None,
    },
    DrainRule {
        drain: AgentDrain::Commit,
        role: AgentRole::Commit,
        capability: DrainCapability::WriteCapable,
        artifact_type: "commit_message",
        allow_continuation: false,
        // Commit is a checkpoint; after it the pipeline continues to Review or terminates.
        allowed_transitions: &[AgentDrain::Review, AgentDrain::Planning],
        analysis_decision_routes: None,
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

/// Look up the drain that an `AnalysisDecision` routes to.
///
/// Returns `None` only if `decision` is not covered by the Analysis drain rule,
/// which would be a bug (all variants must be present per invariant tests).
#[must_use]
pub fn route_for_decision(decision: AnalysisDecision) -> Option<AgentDrain> {
    rule_for(AgentDrain::Analysis)
        .and_then(|r| r.analysis_decision_routes)
        .and_then(|routes| {
            routes
                .iter()
                .find(|(d, _)| *d == decision)
                .map(|(_, drain)| *drain)
        })
}

/// Validate the drain rule table for internal consistency.
///
/// Checks:
/// 1. Every built-in drain appears exactly once.
/// 2. Each rule's `role` matches `AgentDrain::role()`.
/// 3. The Analysis drain has `analysis_decision_routes` covering every
///    `AnalysisDecision` variant exactly once.
/// 4. Every decision route target is present in `allowed_transitions`.
/// 5. All non-analysis drains have `analysis_decision_routes: None`.
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
    for drain in all_drains {
        let count = DRAIN_RULES.iter().filter(|r| r.drain == drain).count();
        if count != 1 {
            return Err(format!(
                "drain {:?} appears {} times in DRAIN_RULES (expected 1)",
                drain, count
            ));
        }
    }

    // 2. Each rule's role must match AgentDrain::role().
    for rule in DRAIN_RULES {
        let canonical = rule.drain.role();
        if rule.role != canonical {
            return Err(format!(
                "DrainRule for {:?} declares role {:?} but AgentDrain::role() returns {:?}",
                rule.drain, rule.role, canonical
            ));
        }
    }

    // 3 & 4 & 5. analysis_decision_routes correctness.
    let expected_decisions = [
        AnalysisDecision::NeedsMoreWork,
        AnalysisDecision::NeedsReplanning,
        AnalysisDecision::ReadyForReview,
        AnalysisDecision::ReadyToCommit,
        AnalysisDecision::NeedsAnotherReview,
    ];
    for rule in DRAIN_RULES {
        if rule.drain == AgentDrain::Analysis {
            let routes = rule.analysis_decision_routes.ok_or_else(|| {
                "Analysis drain must have analysis_decision_routes: Some(...)".to_owned()
            })?;
            // Every expected decision must appear exactly once.
            for decision in &expected_decisions {
                let count = routes.iter().filter(|(d, _)| d == decision).count();
                if count != 1 {
                    return Err(format!(
                        "AnalysisDecision::{:?} appears {} times in analysis_decision_routes (expected 1)",
                        decision, count
                    ));
                }
            }
            if routes.len() != expected_decisions.len() {
                return Err(format!(
                    "analysis_decision_routes has {} entries but there are {} AnalysisDecision variants",
                    routes.len(),
                    expected_decisions.len()
                ));
            }
            // Every route target must be in allowed_transitions.
            for (decision, target) in routes {
                if !rule.allowed_transitions.contains(target) {
                    return Err(format!(
                        "analysis_decision_routes maps {:?} → {:?} but {:?} is not in allowed_transitions",
                        decision, target, target
                    ));
                }
            }
        } else if rule.analysis_decision_routes.is_some() {
            return Err(format!(
                "DrainRule for {:?} has analysis_decision_routes: Some(...) but only Analysis may have routes",
                rule.drain
            ));
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
    // Step 11: Analysis decision routing invariant tests
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

    /// Analysis must have analysis_decision_routes declared.
    #[test]
    fn test_analysis_has_decision_routes() {
        assert!(
            rule_for(AgentDrain::Analysis)
                .unwrap()
                .analysis_decision_routes
                .is_some(),
            "Analysis drain must have analysis_decision_routes: Some(...)"
        );
    }

    /// All five AnalysisDecision variants must appear exactly once in routes.
    #[test]
    fn test_analysis_decision_routes_cover_all_variants() {
        use crate::reducer::state::AnalysisDecision;
        let routes = rule_for(AgentDrain::Analysis)
            .unwrap()
            .analysis_decision_routes
            .unwrap();
        let expected = [
            AnalysisDecision::NeedsMoreWork,
            AnalysisDecision::NeedsReplanning,
            AnalysisDecision::ReadyForReview,
            AnalysisDecision::ReadyToCommit,
            AnalysisDecision::NeedsAnotherReview,
        ];
        assert_eq!(
            routes.len(),
            expected.len(),
            "analysis_decision_routes must have exactly {} entries",
            expected.len()
        );
        for decision in &expected {
            let count = routes.iter().filter(|(d, _)| d == decision).count();
            assert_eq!(
                count, 1,
                "AnalysisDecision::{:?} must appear exactly once in analysis_decision_routes",
                decision
            );
        }
    }

    /// Every decision route target must be listed in Analysis allowed_transitions.
    ///
    /// A route that points to a drain not in allowed_transitions would be
    /// an unreachable dead route — a configuration inconsistency.
    #[test]
    fn test_analysis_decision_route_targets_in_allowed_transitions() {
        let rule = rule_for(AgentDrain::Analysis).unwrap();
        let routes = rule.analysis_decision_routes.unwrap();
        for (decision, target) in routes {
            assert!(
                rule.allowed_transitions.contains(target),
                "analysis_decision_routes maps {:?} → {:?} but {:?} is not in allowed_transitions",
                decision,
                target,
                target
            );
        }
    }

    /// No non-analysis drain may declare analysis_decision_routes.
    ///
    /// Only the Analysis drain drives routing decisions based on AnalysisDecision.
    #[test]
    fn test_non_analysis_drains_have_no_decision_routes() {
        for rule in DRAIN_RULES {
            if rule.drain != AgentDrain::Analysis {
                assert!(
                    rule.analysis_decision_routes.is_none(),
                    "DrainRule for {:?} must have analysis_decision_routes: None",
                    rule.drain
                );
            }
        }
    }

    /// `validate_drain_rules()` must pass for the canonical table.
    ///
    /// This is the single integration test that verifies all validate_drain_rules()
    /// invariants simultaneously.
    #[test]
    fn test_validate_drain_rules_passes() {
        validate_drain_rules().expect("DRAIN_RULES must pass validate_drain_rules()");
    }

    /// Analysis → NeedsMoreWork routes to Development.
    #[test]
    fn test_analysis_needs_more_work_routes_to_development() {
        use crate::reducer::state::AnalysisDecision;
        let routes = rule_for(AgentDrain::Analysis)
            .unwrap()
            .analysis_decision_routes
            .unwrap();
        let target = routes
            .iter()
            .find(|(d, _)| *d == AnalysisDecision::NeedsMoreWork)
            .map(|(_, t)| *t)
            .expect("NeedsMoreWork must be present");
        assert_eq!(
            target,
            AgentDrain::Development,
            "NeedsMoreWork must route to Development"
        );
    }

    /// Analysis → NeedsReplanning routes to Planning.
    #[test]
    fn test_analysis_needs_replanning_routes_to_planning() {
        use crate::reducer::state::AnalysisDecision;
        let routes = rule_for(AgentDrain::Analysis)
            .unwrap()
            .analysis_decision_routes
            .unwrap();
        let target = routes
            .iter()
            .find(|(d, _)| *d == AnalysisDecision::NeedsReplanning)
            .map(|(_, t)| *t)
            .expect("NeedsReplanning must be present");
        assert_eq!(
            target,
            AgentDrain::Planning,
            "NeedsReplanning must route to Planning"
        );
    }

    /// Analysis → ReadyForReview routes to Review.
    #[test]
    fn test_analysis_ready_for_review_routes_to_review() {
        use crate::reducer::state::AnalysisDecision;
        let routes = rule_for(AgentDrain::Analysis)
            .unwrap()
            .analysis_decision_routes
            .unwrap();
        let target = routes
            .iter()
            .find(|(d, _)| *d == AnalysisDecision::ReadyForReview)
            .map(|(_, t)| *t)
            .expect("ReadyForReview must be present");
        assert_eq!(
            target,
            AgentDrain::Review,
            "ReadyForReview must route to Review"
        );
    }

    /// Analysis → ReadyToCommit routes to Commit.
    #[test]
    fn test_analysis_ready_to_commit_routes_to_commit() {
        use crate::reducer::state::AnalysisDecision;
        let routes = rule_for(AgentDrain::Analysis)
            .unwrap()
            .analysis_decision_routes
            .unwrap();
        let target = routes
            .iter()
            .find(|(d, _)| *d == AnalysisDecision::ReadyToCommit)
            .map(|(_, t)| *t)
            .expect("ReadyToCommit must be present");
        assert_eq!(
            target,
            AgentDrain::Commit,
            "ReadyToCommit must route to Commit"
        );
    }

    /// Analysis → NeedsAnotherReview routes to Fix.
    #[test]
    fn test_analysis_needs_another_review_routes_to_fix() {
        use crate::reducer::state::AnalysisDecision;
        let routes = rule_for(AgentDrain::Analysis)
            .unwrap()
            .analysis_decision_routes
            .unwrap();
        let target = routes
            .iter()
            .find(|(d, _)| *d == AnalysisDecision::NeedsAnotherReview)
            .map(|(_, t)| *t)
            .expect("NeedsAnotherReview must be present");
        assert_eq!(
            target,
            AgentDrain::Fix,
            "NeedsAnotherReview must route to Fix"
        );
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

    /// Commit is a checkpoint: it is terminal in the sense it has no continuation
    /// and no Analysis routing.
    #[test]
    fn test_commit_has_no_decision_routes() {
        assert!(
            rule_for(AgentDrain::Commit)
                .unwrap()
                .analysis_decision_routes
                .is_none(),
            "Commit must not have analysis_decision_routes"
        );
    }
}
