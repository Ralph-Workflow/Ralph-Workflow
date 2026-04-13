// Validation helpers for fix result JSON artifacts.
// Split from run_fix.rs to keep file size < 1000 lines.
// This file is included (not mod'd) from run_fix.rs.

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
            crate::reducer::effect::EffectResult::with_ui(
                PipelineEvent::fix_result_xml_validated_with_decision(
                    pass,
                    status,
                    Some(elements.summary),
                    elements.analysis_decision,
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
) -> EffectResult {
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
) -> Option<EffectResult> {
    match ctx.workspace.read_artifact_json(json_type) {
        Ok(Some(envelope)) => Some(validate_fix_json_envelope(
            pass,
            envelope,
            is_analysis,
            fix_analysis_continuation,
            invalid_attempts,
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
