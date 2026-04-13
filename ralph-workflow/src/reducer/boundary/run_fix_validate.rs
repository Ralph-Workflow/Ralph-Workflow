// Validation helpers for fix result JSON artifacts.
// Split from run_fix.rs to keep file size < 1000 lines.
// This file is included (not mod'd) from run_fix.rs.

/// Convert a `DevelopmentAnalysisDecision` from a development_result artifact to the
/// equivalent `ReviewAnalysisDecision` for the review cycle.
///
/// The analysis agent currently produces development_result artifacts in fix-analysis
/// context (using the development_result schema). The decisions map semantically:
/// - NeedsMoreWork → NeedsMoreFix (stay in fix cycle)
/// - CycleComplete → CycleComplete (proceed to review_commit)
fn to_review_analysis_decision(
    d: crate::reducer::state::DevelopmentAnalysisDecision,
) -> crate::reducer::state::ReviewAnalysisDecision {
    use crate::reducer::state::{DevelopmentAnalysisDecision, ReviewAnalysisDecision};
    match d {
        DevelopmentAnalysisDecision::NeedsMoreWork => ReviewAnalysisDecision::NeedsMoreFix,
        DevelopmentAnalysisDecision::CycleComplete => ReviewAnalysisDecision::CycleComplete,
    }
}

fn fix_json_artifact_present(ctx: &PhaseContext<'_>, is_analysis: bool) -> bool {
    let json_type = fix_json_artifact_type(is_analysis);
    !matches!(ctx.workspace.read_artifact_json(json_type), Ok(None))
}

fn fix_json_artifact_type(is_analysis: bool) -> &'static str {
    if is_analysis {
        "development_result"
    } else {
        "fix_result"
    }
}

/// Bundles identity validation parameters to reduce argument count.
struct ArtifactIdentity<'a> {
    current_drain: crate::agents::AgentDrain,
    run_id: &'a str,
    logger: &'a crate::logger::Logger,
}

fn apply_and_build_analysis_result(
    pass: u32,
    elements: crate::files::result_types::DevelopmentResultElements,
    fix_analysis_continuation: bool,
    invalid_attempts: u32,
) -> EffectResult {
    match apply_fix_analysis_continuation_contract_if_needed(elements, fix_analysis_continuation) {
        Ok(elements) => {
            let status = parse_development_result_status(&elements.status);
            let json_note = String::from("[validated from JSON artifact]");
            // Convert DevelopmentAnalysisDecision → ReviewAnalysisDecision at the boundary:
            // fix analysis currently reads development_result artifacts but must route
            // using review-cycle semantics (NeedsMoreFix / CycleComplete).
            let review_decision = elements.analysis_decision.map(to_review_analysis_decision);
            crate::reducer::effect::EffectResult::with_ui(
                PipelineEvent::fix_result_xml_validated_with_decision(
                    pass,
                    status,
                    Some(elements.summary),
                    review_decision,
                ),
                vec![UIEvent::XmlOutput {
                    xml_type: XmlOutputType::DevelopmentResult,
                    content: json_note,
                    context: Some(XmlOutputContext {
                        iteration: None,
                        pass: Some(pass),
                        snippets: Vec::new(),
                    }),
                }],
            )
        }
        Err(err) => EffectResult::event(PipelineEvent::fix_output_validation_failed(
            pass,
            invalid_attempts,
            Some(err),
        )),
    }
}

fn try_validate_fix_analysis_json(
    pass: u32,
    envelope: &crate::workspace::ArtifactEnvelope,
    fix_analysis_continuation: bool,
    invalid_attempts: u32,
) -> EffectResult {
    match super::json_artifact::development_result_from_envelope(envelope) {
        Ok(elements) => {
            apply_and_build_analysis_result(pass, elements, fix_analysis_continuation, invalid_attempts)
        }
        Err(err) => EffectResult::event(PipelineEvent::fix_output_validation_failed(
            pass,
            invalid_attempts,
            Some(err.to_string()),
        )),
    }
}

fn try_validate_fix_normal_json(
    pass: u32,
    envelope: &crate::workspace::ArtifactEnvelope,
    invalid_attempts: u32,
) -> EffectResult {
    match super::json_artifact::fix_result_from_envelope(envelope) {
        Ok(elements) => {
            let status = crate::reducer::state::FixStatus::parse(&elements.status)
                .unwrap_or(crate::reducer::state::FixStatus::Failed);
            let json_note = String::from("[validated from JSON artifact]");
            crate::reducer::effect::EffectResult::with_ui(
                PipelineEvent::fix_result_xml_validated(pass, status, elements.summary),
                vec![UIEvent::XmlOutput {
                    xml_type: XmlOutputType::FixResult,
                    content: json_note,
                    context: Some(XmlOutputContext {
                        iteration: None,
                        pass: Some(pass),
                        snippets: Vec::new(),
                    }),
                }],
            )
        }
        Err(err) => EffectResult::event(PipelineEvent::fix_output_validation_failed(
            pass,
            invalid_attempts,
            Some(err.to_string()),
        )),
    }
}

fn validate_fix_json_envelope(
    pass: u32,
    envelope: crate::workspace::ArtifactEnvelope,
    is_analysis: bool,
    fix_analysis_continuation: bool,
    invalid_attempts: u32,
    identity: ArtifactIdentity<'_>,
) -> EffectResult {
    // Validate envelope identity to reject stale or misrouted artifacts.
    // Only enforce checks when the envelope has the identity fields set.
    // This allows backward compatibility with artifacts that don't yet have identity metadata.
    let run_id_mismatch = envelope
        .run_id
        .as_ref()
        .is_some_and(|id| id != identity.run_id);
    let drain_mismatch = envelope
        .drain
        .as_ref()
        .is_some_and(|d| d != identity.current_drain.as_str());

    if run_id_mismatch || drain_mismatch {
        let reason = if run_id_mismatch && drain_mismatch {
            format!(
                "run_id mismatch (expected {}, got {:?}) and drain mismatch (expected {}, got {:?})",
                identity.run_id,
                envelope.run_id,
                identity.current_drain.as_str(),
                envelope.drain
            )
        } else if run_id_mismatch {
            format!("run_id mismatch (expected {}, got {:?})", identity.run_id, envelope.run_id)
        } else {
            format!(
                "drain mismatch (expected {}, got {:?})",
                identity.current_drain.as_str(),
                envelope.drain
            )
        };
        identity.logger.warn(&format!(
            "Fix artifact rejected: {} (run_id={}, drain={})",
            reason,
            identity.run_id,
            identity.current_drain.as_str()
        ));
        return EffectResult::event(PipelineEvent::fix_output_validation_failed(
            pass,
            invalid_attempts,
            Some(format!("artifact identity check failed: {}", reason)),
        ));
    }
    if is_analysis {
        try_validate_fix_analysis_json(pass, &envelope, fix_analysis_continuation, invalid_attempts)
    } else {
        try_validate_fix_normal_json(pass, &envelope, invalid_attempts)
    }
}

fn try_validate_fix_from_json(
    ctx: &PhaseContext<'_>,
    pass: u32,
    is_analysis: bool,
    fix_analysis_continuation: bool,
    invalid_attempts: u32,
    json_type: &str,
    identity: ArtifactIdentity<'_>,
) -> Option<EffectResult> {
    match ctx.workspace.read_artifact_json(json_type) {
        Ok(Some(envelope)) => Some(validate_fix_json_envelope(
            pass,
            envelope,
            is_analysis,
            fix_analysis_continuation,
            invalid_attempts,
            identity,
        )),
        Ok(None) => None,
        Err(err) => Some(EffectResult::event(
            PipelineEvent::fix_output_validation_failed(
                pass,
                invalid_attempts,
                Some(format!("Invalid JSON artifact '{json_type}': {err}")),
            ),
        )),
    }
}

fn maybe_append_fix_invoked_event(
    result: crate::reducer::effect::EffectResult,
    pass: u32,
) -> crate::reducer::effect::EffectResult {
    let succeeded = result.additional_events.iter().any(|e| {
        matches!(
            e,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        )
    });
    if succeeded {
        result.with_additional_event(PipelineEvent::fix_agent_invoked(pass))
    } else {
        result
    }
}

fn apply_fix_analysis_continuation_contract_if_needed(
    elements: crate::files::result_types::DevelopmentResultElements,
    continuation_mode: bool,
) -> Result<crate::files::result_types::DevelopmentResultElements, String> {
    if continuation_mode {
        crate::files::result_types::apply_continuation_development_result_contract(elements)
    } else {
        Ok(elements)
    }
}
