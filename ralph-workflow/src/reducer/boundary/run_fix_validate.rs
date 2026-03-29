// Validation helpers for fix result XML and JSON artifacts.
// Split from run_fix.rs to keep file size < 1000 lines.
// This file is included (not mod'd) from run_fix.rs.

fn fix_json_artifact_present(ctx: &PhaseContext<'_>, is_analysis: bool) -> bool {
    let json_type = fix_json_artifact_type(is_analysis);
    !matches!(ctx.workspace.read_artifact_json(json_type), Ok(None))
}

fn extract_fix_result_xml_from_disk(
    ctx: &PhaseContext<'_>,
    pass: u32,
    is_analysis: bool,
    invalid_attempts: u32,
) -> EffectResult {
    match read_xml_for_pass(ctx, is_analysis) {
        Ok(_) => EffectResult::event(PipelineEvent::fix_result_xml_extracted(pass)),
        Err(err) => EffectResult::event(PipelineEvent::fix_result_xml_missing(
            pass,
            invalid_attempts,
            xml_io_error_detail(&err),
        )),
    }
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
    elements: crate::files::llm_output_extraction::DevelopmentResultElements,
    fix_analysis_continuation: bool,
    invalid_attempts: u32,
) -> EffectResult {
    match apply_fix_analysis_continuation_contract_if_needed(elements, fix_analysis_continuation) {
        Ok(elements) => {
            let status = parse_development_result_status(&elements.status);
            let json_note = String::from("[validated from JSON artifact]");
            crate::reducer::effect::EffectResult::with_ui(
                PipelineEvent::fix_result_xml_validated(pass, status, Some(elements.summary)),
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
            Some(err.format_for_ai_retry()),
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

fn validate_fix_from_xml(
    ctx: &PhaseContext<'_>,
    pass: u32,
    is_analysis: bool,
    fix_analysis_continuation: bool,
    invalid_attempts: u32,
) -> EffectResult {
    match read_xml_for_pass(ctx, is_analysis) {
        Ok(xml_content) => validate_fix_xml_content(
            pass,
            xml_content,
            is_analysis,
            fix_analysis_continuation,
            invalid_attempts,
        ),
        Err(err) => EffectResult::event(PipelineEvent::fix_output_validation_failed(
            pass,
            invalid_attempts,
            xml_io_error_detail(&err),
        )),
    }
}

fn validate_fix_xml_content(
    pass: u32,
    xml_content: String,
    is_analysis: bool,
    fix_analysis_continuation: bool,
    invalid_attempts: u32,
) -> EffectResult {
    if is_analysis {
        validate_fix_analysis_xml(pass, xml_content, invalid_attempts, fix_analysis_continuation)
    } else {
        validate_fix_normal_xml(pass, xml_content, invalid_attempts)
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

fn build_fix_analysis_xml_ok_result(
    pass: u32,
    elements: crate::files::llm_output_extraction::DevelopmentResultElements,
    xml_content: String,
) -> crate::reducer::effect::EffectResult {
    let status = parse_development_result_status(&elements.status);
    crate::reducer::effect::EffectResult::with_ui(
        PipelineEvent::fix_result_xml_validated(pass, status, Some(elements.summary)),
        vec![UIEvent::XmlOutput {
            xml_type: XmlOutputType::DevelopmentResult,
            content: xml_content,
            context: Some(XmlOutputContext {
                iteration: None,
                pass: Some(pass),
                snippets: Vec::new(),
            }),
        }],
    )
}

fn fix_validation_failed(
    pass: u32,
    invalid_attempts: u32,
    err_msg: String,
) -> crate::reducer::effect::EffectResult {
    crate::reducer::effect::EffectResult::event(PipelineEvent::fix_output_validation_failed(
        pass,
        invalid_attempts,
        Some(err_msg),
    ))
}

fn apply_continuation_to_fix_analysis(
    pass: u32,
    xml_content: String,
    invalid_attempts: u32,
    continuation_mode: bool,
    elements: crate::files::llm_output_extraction::DevelopmentResultElements,
) -> crate::reducer::effect::EffectResult {
    match apply_fix_analysis_continuation_contract_if_needed(elements, continuation_mode) {
        Ok(elements) => build_fix_analysis_xml_ok_result(pass, elements, xml_content),
        Err(err) => fix_validation_failed(pass, invalid_attempts, err.format_for_ai_retry()),
    }
}

fn validate_fix_analysis_xml(
    pass: u32,
    xml_content: String,
    invalid_attempts: u32,
    continuation_mode: bool,
) -> crate::reducer::effect::EffectResult {
    use crate::files::llm_output_extraction::validate_development_result_xml;
    match validate_development_result_xml(&xml_content) {
        Ok(elements) => apply_continuation_to_fix_analysis(
            pass,
            xml_content,
            invalid_attempts,
            continuation_mode,
            elements,
        ),
        Err(err) => fix_validation_failed(pass, invalid_attempts, err.format_for_ai_retry()),
    }
}

fn apply_fix_analysis_continuation_contract_if_needed(
    elements: crate::files::llm_output_extraction::DevelopmentResultElements,
    continuation_mode: bool,
) -> Result<
    crate::files::llm_output_extraction::DevelopmentResultElements,
    crate::files::llm_output_extraction::xsd_validation::XsdValidationError,
> {
    if continuation_mode {
        crate::files::llm_output_extraction::apply_continuation_development_result_contract(
            elements,
        )
    } else {
        Ok(elements)
    }
}

fn validate_fix_normal_xml(
    pass: u32,
    xml_content: String,
    invalid_attempts: u32,
) -> crate::reducer::effect::EffectResult {
    use crate::files::llm_output_extraction::validate_fix_result_xml;
    match validate_fix_result_xml(&xml_content) {
        Ok(elements) => {
            let status = crate::reducer::state::FixStatus::parse(&elements.status)
                .unwrap_or(crate::reducer::state::FixStatus::Failed);
            crate::reducer::effect::EffectResult::with_ui(
                PipelineEvent::fix_result_xml_validated(pass, status, elements.summary),
                vec![UIEvent::XmlOutput {
                    xml_type: XmlOutputType::FixResult,
                    content: xml_content,
                    context: Some(XmlOutputContext {
                        iteration: None,
                        pass: Some(pass),
                        snippets: Vec::new(),
                    }),
                }],
            )
        }
        Err(err) => crate::reducer::effect::EffectResult::event(
            PipelineEvent::fix_output_validation_failed(
                pass,
                invalid_attempts,
                Some(err.format_for_ai_retry()),
            ),
        ),
    }
}
