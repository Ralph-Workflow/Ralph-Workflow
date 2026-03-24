//! Review phase effect handlers.
//!
//! Split boundary handlers for review/fix flows.

use super::MainEffectHandler;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::review::boundary_domain::{
    derive_review_validation_flags, render_issues_markdown, review_outcome_event,
    should_materialize_xsd_retry_last_output,
};
use crate::phases::PhaseContext;
use crate::prompts::content_reference::MAX_INLINE_CONTENT_SIZE;
use crate::reducer::domain::baseline::parse_baseline_oid;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{AgentEvent, ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::{
    MaterializedPromptInput, PromptInputKind, PromptInputRepresentation,
    PromptMaterializationReason,
};
use crate::reducer::ui_event::{UIEvent, XmlCodeSnippet, XmlOutputContext, XmlOutputType};
use anyhow::Result;
use std::path::Path;

impl MainEffectHandler {
    pub(super) const DIFF_BASELINE_PATH: &str = ".agent/DIFF.base";
}

impl MainEffectHandler {
    pub(super) fn sentinel_plan_content(isolation_mode: bool) -> String {
        crate::phases::review::boundary_domain::sentinel_plan_content(isolation_mode)
    }

    pub(super) fn fallback_diff_instructions(baseline_oid: &str) -> String {
        let baseline_oid_option = parse_baseline_oid(baseline_oid).ok();
        crate::phases::review::boundary_domain::fallback_diff_instructions(
            baseline_oid_option.as_ref(),
        )
    }

    pub(super) fn prepare_review_context(&self, ctx: &PhaseContext<'_>, pass: u32) -> EffectResult {
        log_prompt_backup_result(ctx);
        let (diff, baseline_oid) = fetch_review_diff(ctx);
        write_review_baseline(ctx, Self::DIFF_BASELINE_PATH, &diff, &baseline_oid);
        EffectResult::with_ui(
            PipelineEvent::review_context_prepared(pass),
            vec![UIEvent::ReviewProgress {
                pass,
                total: self.state.total_reviewer_passes,
            }],
        )
    }

    pub(super) fn materialize_review_inputs(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> Result<EffectResult> {
        let plan_content = read_review_plan_content(ctx, ctx.config.isolation_mode)?;
        let baseline_oid = read_review_baseline_oid(ctx, Self::DIFF_BASELINE_PATH)?;
        let diff_content = read_review_diff_content(ctx, &baseline_oid)?;

        let inline_budget_bytes = MAX_INLINE_CONTENT_SIZE as u64;
        let consumer_signature_sha256 = self.state.agent_chain.consumer_signature_sha256();

        let (plan_representation, plan_reason) =
            compute_plan_representation(ctx, &plan_content, inline_budget_bytes);
        let (diff_representation, diff_reason) =
            compute_diff_representation(ctx, &diff_content, inline_budget_bytes)?;

        let plan_input = build_materialized_plan_input(
            &plan_content,
            consumer_signature_sha256.clone(),
            inline_budget_bytes,
            plan_representation,
            plan_reason,
        );
        let diff_input = build_materialized_diff_input(
            &diff_content,
            consumer_signature_sha256,
            inline_budget_bytes,
            diff_representation,
            diff_reason,
        );

        Ok(build_review_materialized_result(
            pass,
            plan_input,
            diff_input,
            inline_budget_bytes,
        ))
    }

    pub(super) fn invoke_review_agent(
        &mut self,
        ctx: &mut PhaseContext<'_>,
        pass: u32,
    ) -> Result<EffectResult> {
        use crate::agents::AgentRole;

        self.normalize_agent_chain_for_invocation(ctx, crate::agents::AgentDrain::Review);

        let prompt = read_review_prompt_file(ctx, pass)?;
        let agent = resolve_reviewer_agent(&self.state, ctx);

        // RFC-009: The closure receives the AgentSession created by invoke_agent.
        // In V1, session capabilities == drain defaults, so the pre-generated prompt
        // is correct. The closure still calls capability_template_variables_from_session
        // to verify the V1 invariant holds and to exercise the RFC-009 session-aware path.
        let result = self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::Review,
            AgentRole::Reviewer,
            &agent,
            None,
            |session: &crate::agents::session::AgentSession| {
                let _session_vars =
                    crate::prompts::capability_template_variables_from_session(session);
                prompt.clone()
            },
        )?;
        Ok(append_review_invoked_event(result, pass))
    }

    pub(super) fn extract_review_issues_xml(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> EffectResult {
        let issues_xml = Path::new(xml_paths::ISSUES_XML);
        match ctx.workspace.read(issues_xml) {
            Ok(_) => EffectResult::event(PipelineEvent::review_issues_xml_extracted(pass)),
            Err(err) => issues_xml_missing_result(
                pass,
                self.state.continuation.invalid_output_attempts,
                &err,
            ),
        }
    }

    pub(super) fn validate_review_issues_xml(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> EffectResult {
        let invalid_output_attempts = self.state.continuation.invalid_output_attempts;
        let issues_xml =
            match read_review_issues_xml_for_validation(ctx, invalid_output_attempts, pass) {
                Ok(s) => s,
                Err(result) => return *result,
            };
        validate_and_build_result(pass, issues_xml, invalid_output_attempts)
    }

    pub(super) fn write_issues_markdown(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> Result<EffectResult> {
        use crate::files::llm_output_extraction::IssuesElements;

        let outcome = self
            .state
            .review_validated_outcome
            .as_ref()
            .filter(|outcome| outcome.pass == pass)
            .ok_or(ErrorEvent::ValidatedReviewOutcomeMissing { pass })?;

        let elements = ctx
            .workspace
            .read(Path::new(xml_paths::ISSUES_XML))
            .ok()
            .and_then(|xml| crate::files::llm_output_extraction::validate_issues_xml(&xml).ok())
            .unwrap_or_else(|| IssuesElements {
                issues: outcome
                    .issues
                    .iter()
                    .map(|s| crate::files::llm_output_extraction::IssueEntry {
                        text: s.clone(),
                        skills_mcp: None,
                    })
                    .collect(),
                no_issues_found: outcome.no_issues_found.clone(),
            });

        let markdown = render_issues_markdown(&elements);
        ctx.workspace
            .write(Path::new(".agent/ISSUES.md"), &markdown)
            .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
                path: ".agent/ISSUES.md".to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;

        Ok(EffectResult::event(
            PipelineEvent::review_issues_markdown_written(pass),
        ))
    }

    pub(super) fn extract_review_issue_snippets(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> Result<EffectResult> {
        let outcome = self
            .state
            .review_validated_outcome
            .as_ref()
            .filter(|outcome| outcome.pass == pass)
            .ok_or(ErrorEvent::ValidatedReviewOutcomeMissing { pass })?;

        let issues_xml = ctx.workspace.read(Path::new(xml_paths::ISSUES_XML));
        let issues_xml = match issues_xml {
            Ok(s) => s,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                ctx.logger
                    .warn("Missing .agent/tmp/issues.xml; using empty content for UI output");
                String::new()
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: xml_paths::ISSUES_XML.to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into());
            }
        };

        let snippets = extract_issue_snippets(&outcome.issues, ctx.workspace);
        Ok(EffectResult::with_ui(
            PipelineEvent::review_issue_snippets_extracted(pass),
            vec![UIEvent::XmlOutput {
                xml_type: XmlOutputType::ReviewIssues,
                content: issues_xml,
                context: Some(XmlOutputContext {
                    iteration: None,
                    pass: Some(pass),
                    snippets,
                }),
            }],
        ))
    }

    pub(super) fn archive_review_issues_xml(ctx: &PhaseContext<'_>, pass: u32) -> EffectResult {
        use crate::files::llm_output_extraction::archive_xml_file_with_workspace;

        archive_xml_file_with_workspace(ctx.workspace, Path::new(xml_paths::ISSUES_XML));
        EffectResult::event(PipelineEvent::review_issues_xml_archived(pass))
    }

    pub(super) fn apply_review_outcome(
        _ctx: &mut PhaseContext<'_>,
        pass: u32,
        issues_found: bool,
        clean_no_issues: bool,
    ) -> EffectResult {
        EffectResult::event(review_outcome_event(pass, issues_found, clean_no_issues))
    }

    pub(super) fn materialize_xsd_retry_last_output(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> Result<Vec<PipelineEvent>> {
        use crate::phases::review::boundary_domain::XsdRetryMaterializationSignature;

        let primary_path = Path::new(xml_paths::ISSUES_XML);
        let archived_path = Path::new(".agent/tmp/issues.xml.processed");
        let last_output = read_xsd_retry_source(ctx, primary_path, archived_path)?;

        let content_id_sha256 = sha256_hex_str(&last_output);
        let consumer_signature_sha256 = self.state.agent_chain.consumer_signature_sha256();
        let inline_budget_bytes = MAX_INLINE_CONTENT_SIZE as u64;
        let last_output_bytes = last_output.len() as u64;

        let candidate = XsdRetryMaterializationSignature {
            phase: crate::reducer::event::PipelinePhase::Review,
            scope_id: pass,
            content_id_sha256: content_id_sha256.clone(),
            consumer_signature_sha256,
        };

        let existing = find_existing_xsd_signature(&self.state, pass, &candidate);

        if should_materialize_xsd_retry_last_output(existing.as_ref(), &candidate) {
            let last_output_path = Path::new(".agent/tmp/last_output.xml");
            write_xsd_last_output(ctx, last_output_path, &last_output)?;

            let input = build_xsd_last_output_input(
                &candidate,
                last_output_bytes,
                inline_budget_bytes,
                last_output_path,
            );

            Ok(build_xsd_materialized_events(
                input,
                pass,
                &content_id_sha256,
                last_output_bytes,
                inline_budget_bytes,
            ))
        } else {
            Ok(Vec::new())
        }
    }
}

fn log_prompt_backup_result(ctx: &PhaseContext<'_>) {
    use crate::files::create_prompt_backup_with_workspace;
    match create_prompt_backup_with_workspace(ctx.workspace) {
        Ok(Some(warning)) => ctx
            .logger
            .warn(&format!("PROMPT.md backup created with warning: {warning}")),
        Ok(None) => {}
        Err(err) => ctx
            .logger
            .warn(&format!("Failed to create PROMPT.md backup: {err}")),
    }
}

fn fetch_review_diff(ctx: &PhaseContext<'_>) -> (String, String) {
    use crate::files::write_diff_backup_with_workspace;
    let (diff, baseline_oid) =
        match crate::git_helpers::get_git_diff_for_review_with_workspace(ctx.workspace) {
            Ok((diff, baseline_oid)) => (diff, baseline_oid),
            Err(err) => {
                ctx.logger
                    .warn(&format!("Failed to compute review diff: {err}"));
                (String::new(), String::new())
            }
        };
    if let Err(err) = write_diff_backup_with_workspace(ctx.workspace, &diff) {
        ctx.logger
            .warn(&format!("Failed to write .agent/DIFF.backup: {err}"));
    }
    (diff, baseline_oid)
}

fn write_review_baseline(
    ctx: &PhaseContext<'_>,
    baseline_path_str: &str,
    _diff: &str,
    baseline_oid: &str,
) {
    let baseline_path = Path::new(baseline_path_str);
    match parse_baseline_oid(baseline_oid) {
        Ok(parsed_baseline) => {
            if let Err(err) = ctx.workspace.write(baseline_path, parsed_baseline.as_str()) {
                ctx.logger
                    .warn(&format!("Failed to write review diff baseline: {err}"));
            }
        }
        Err(_) => {
            let _ = ctx.workspace.remove_if_exists(baseline_path);
        }
    }
}

fn read_review_plan_content(
    ctx: &PhaseContext<'_>,
    is_isolation_mode: bool,
) -> std::result::Result<String, anyhow::Error> {
    match ctx.workspace.read(Path::new(".agent/PLAN.md")) {
        Ok(plan_content) => Ok(plan_content),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            write_sentinel_plan_content(ctx, is_isolation_mode)
        }
        Err(err) => Err(ErrorEvent::WorkspaceReadFailed {
            path: ".agent/PLAN.md".to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        }
        .into()),
    }
}

fn write_sentinel_plan_content(
    ctx: &PhaseContext<'_>,
    is_isolation_mode: bool,
) -> std::result::Result<String, anyhow::Error> {
    use super::MainEffectHandler;
    ctx.logger
        .warn("Missing .agent/PLAN.md; using sentinel PLAN content for review");
    let sentinel = MainEffectHandler::sentinel_plan_content(is_isolation_mode);
    ensure_agent_dir(ctx)?;
    ctx.workspace
        .write(Path::new(".agent/PLAN.md"), &sentinel)
        .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
            path: ".agent/PLAN.md".to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        })?;
    Ok(sentinel)
}

fn ensure_agent_dir(ctx: &PhaseContext<'_>) -> std::result::Result<(), anyhow::Error> {
    let agent_dir = Path::new(".agent");
    if !ctx.workspace.exists(agent_dir) {
        ctx.workspace.create_dir_all(agent_dir).map_err(|err| {
            ErrorEvent::WorkspaceCreateDirAllFailed {
                path: agent_dir.display().to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            }
        })?;
    }
    Ok(())
}

fn read_review_baseline_oid(
    ctx: &PhaseContext<'_>,
    baseline_path: &str,
) -> std::result::Result<String, anyhow::Error> {
    match ctx.workspace.read(Path::new(baseline_path)) {
        Ok(s) => Ok(s.trim().to_string()),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(String::new()),
        Err(err) => Err(ErrorEvent::WorkspaceReadFailed {
            path: baseline_path.to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        }
        .into()),
    }
}

fn read_review_diff_content(
    ctx: &PhaseContext<'_>,
    baseline_oid: &str,
) -> std::result::Result<String, anyhow::Error> {
    use super::MainEffectHandler;
    match ctx.workspace.read(Path::new(".agent/DIFF.backup")) {
        Ok(diff_content) => Ok(diff_content),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            ctx.logger
                .warn("Missing .agent/DIFF.backup; providing git diff fallback instructions");
            Ok(MainEffectHandler::fallback_diff_instructions(baseline_oid))
        }
        Err(err) => Err(ErrorEvent::WorkspaceReadFailed {
            path: ".agent/DIFF.backup".to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        }
        .into()),
    }
}

fn compute_plan_representation(
    ctx: &PhaseContext<'_>,
    plan_content: &str,
    inline_budget_bytes: u64,
) -> (PromptInputRepresentation, PromptMaterializationReason) {
    let plan_path = Path::new(".agent/PLAN.md");
    if plan_content.len() as u64 > inline_budget_bytes {
        ctx.logger.warn(&format!(
            "PLAN size ({} KB) exceeds inline limit ({} KB). Referencing: {}",
            (plan_content.len() as u64) / 1024,
            inline_budget_bytes / 1024,
            plan_path.display()
        ));
        (
            PromptInputRepresentation::FileReference {
                path: plan_path.to_path_buf(),
            },
            PromptMaterializationReason::InlineBudgetExceeded,
        )
    } else {
        (
            PromptInputRepresentation::Inline,
            PromptMaterializationReason::WithinBudgets,
        )
    }
}

fn compute_diff_representation(
    ctx: &PhaseContext<'_>,
    diff_content: &str,
    inline_budget_bytes: u64,
) -> std::result::Result<(PromptInputRepresentation, PromptMaterializationReason), anyhow::Error> {
    if diff_content.len() as u64 > inline_budget_bytes {
        write_oversize_diff_file(ctx, diff_content, inline_budget_bytes)
    } else {
        Ok((
            PromptInputRepresentation::Inline,
            PromptMaterializationReason::WithinBudgets,
        ))
    }
}

fn write_oversize_diff_file(
    ctx: &PhaseContext<'_>,
    diff_content: &str,
    inline_budget_bytes: u64,
) -> std::result::Result<(PromptInputRepresentation, PromptMaterializationReason), anyhow::Error> {
    let diff_path = Path::new(".agent/tmp/diff.txt");
    ensure_tmp_agent_dir(ctx)?;
    ctx.workspace
        .write_atomic(diff_path, diff_content)
        .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
            path: ".agent/tmp/diff.txt".to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        })?;
    ctx.logger.warn(&format!(
        "DIFF size ({} KB) exceeds inline limit ({} KB). Referencing: {}",
        (diff_content.len() as u64) / 1024,
        inline_budget_bytes / 1024,
        diff_path.display()
    ));
    Ok((
        PromptInputRepresentation::FileReference {
            path: diff_path.to_path_buf(),
        },
        PromptMaterializationReason::InlineBudgetExceeded,
    ))
}

fn ensure_tmp_agent_dir(ctx: &PhaseContext<'_>) -> std::result::Result<(), anyhow::Error> {
    let tmp_dir = Path::new(".agent/tmp");
    if !ctx.workspace.exists(tmp_dir) {
        ctx.workspace.create_dir_all(tmp_dir).map_err(|err| {
            ErrorEvent::WorkspaceCreateDirAllFailed {
                path: tmp_dir.display().to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            }
        })?;
    }
    Ok(())
}

fn build_materialized_plan_input(
    plan_content: &str,
    consumer_signature_sha256: String,
    inline_budget_bytes: u64,
    representation: PromptInputRepresentation,
    reason: PromptMaterializationReason,
) -> MaterializedPromptInput {
    MaterializedPromptInput {
        kind: PromptInputKind::Plan,
        content_id_sha256: sha256_hex_str(plan_content),
        consumer_signature_sha256,
        original_bytes: plan_content.len() as u64,
        final_bytes: plan_content.len() as u64,
        model_budget_bytes: None,
        inline_budget_bytes: Some(inline_budget_bytes),
        representation,
        reason,
    }
}

fn build_materialized_diff_input(
    diff_content: &str,
    consumer_signature_sha256: String,
    inline_budget_bytes: u64,
    representation: PromptInputRepresentation,
    reason: PromptMaterializationReason,
) -> MaterializedPromptInput {
    MaterializedPromptInput {
        kind: PromptInputKind::Diff,
        content_id_sha256: sha256_hex_str(diff_content),
        consumer_signature_sha256,
        original_bytes: diff_content.len() as u64,
        final_bytes: diff_content.len() as u64,
        model_budget_bytes: None,
        inline_budget_bytes: Some(inline_budget_bytes),
        representation,
        reason,
    }
}

fn build_review_materialized_result(
    pass: u32,
    plan_input: MaterializedPromptInput,
    diff_input: MaterializedPromptInput,
    inline_budget_bytes: u64,
) -> EffectResult {
    let result = EffectResult::event(PipelineEvent::review_inputs_materialized(
        pass,
        plan_input.clone(),
        diff_input.clone(),
    ));
    let result = attach_oversize_plan_event(result, &plan_input, inline_budget_bytes);
    attach_oversize_diff_event(result, &diff_input, inline_budget_bytes)
}

fn attach_oversize_plan_event(
    result: EffectResult,
    plan_input: &MaterializedPromptInput,
    inline_budget_bytes: u64,
) -> EffectResult {
    use crate::reducer::event::PipelinePhase;
    if plan_input.original_bytes > inline_budget_bytes {
        result
            .with_ui_event(UIEvent::AgentActivity {
                agent: "pipeline".to_string(),
                message: format!(
                    "Oversize PLAN: {} KB > {} KB; using file reference",
                    plan_input.original_bytes / 1024,
                    inline_budget_bytes / 1024
                ),
            })
            .with_additional_event(PipelineEvent::prompt_input_oversize_detected(
                PipelinePhase::Review,
                PromptInputKind::Plan,
                plan_input.content_id_sha256.clone(),
                plan_input.original_bytes,
                inline_budget_bytes,
                "inline-embedding".to_string(),
            ))
    } else {
        result
    }
}

fn attach_oversize_diff_event(
    result: EffectResult,
    diff_input: &MaterializedPromptInput,
    inline_budget_bytes: u64,
) -> EffectResult {
    use crate::reducer::event::PipelinePhase;
    if diff_input.original_bytes > inline_budget_bytes {
        result
            .with_ui_event(UIEvent::AgentActivity {
                agent: "pipeline".to_string(),
                message: format!(
                    "Oversize DIFF: {} KB > {} KB; using file reference",
                    diff_input.original_bytes / 1024,
                    inline_budget_bytes / 1024
                ),
            })
            .with_additional_event(PipelineEvent::prompt_input_oversize_detected(
                PipelinePhase::Review,
                PromptInputKind::Diff,
                diff_input.content_id_sha256.clone(),
                diff_input.original_bytes,
                inline_budget_bytes,
                "inline-embedding".to_string(),
            ))
    } else {
        result
    }
}

fn read_review_issues_xml_for_validation(
    ctx: &PhaseContext<'_>,
    invalid_output_attempts: u32,
    pass: u32,
) -> std::result::Result<String, Box<EffectResult>> {
    let issues_xml = ctx.workspace.read(Path::new(xml_paths::ISSUES_XML));
    match issues_xml {
        Ok(s) => Ok(s),
        Err(err) => {
            let detail = extract_workspace_error_detail(&err);
            Err(Box::new(EffectResult::event(
                PipelineEvent::review_output_validation_failed(
                    pass,
                    invalid_output_attempts,
                    detail,
                ),
            )))
        }
    }
}

fn issues_xml_missing_result(
    pass: u32,
    invalid_output_attempts: u32,
    err: &std::io::Error,
) -> EffectResult {
    let detail = extract_workspace_error_detail(err);
    EffectResult::event(PipelineEvent::review_issues_xml_missing(
        pass,
        invalid_output_attempts,
        detail,
    ))
}

fn extract_workspace_error_detail(err: &std::io::Error) -> Option<String> {
    if err.kind() == std::io::ErrorKind::NotFound {
        None
    } else {
        Some(format!(
            "{:?}: {}",
            WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            err
        ))
    }
}

// --- invoke_review_agent helpers ---

fn read_review_prompt_file(ctx: &PhaseContext<'_>, pass: u32) -> Result<String> {
    match ctx
        .workspace
        .read(Path::new(".agent/tmp/review_prompt.txt"))
    {
        Ok(s) => Ok(s),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            Err(ErrorEvent::ReviewPromptMissing { pass }.into())
        }
        Err(err) => Err(ErrorEvent::WorkspaceReadFailed {
            path: ".agent/tmp/review_prompt.txt".to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        }
        .into()),
    }
}

fn resolve_reviewer_agent(
    state: &crate::reducer::state::PipelineState,
    ctx: &PhaseContext<'_>,
) -> String {
    state
        .agent_chain
        .current_agent()
        .cloned()
        .unwrap_or_else(|| ctx.reviewer_agent.to_string())
}

fn append_review_invoked_event(result: EffectResult, pass: u32) -> EffectResult {
    if result.additional_events.iter().any(|e| {
        matches!(
            e,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        )
    }) {
        result
            .clone()
            .with_additional_event(PipelineEvent::review_agent_invoked(pass))
    } else {
        result
    }
}

// --- validate_review_issues_xml helpers ---

fn validate_and_build_result(
    pass: u32,
    issues_xml: String,
    invalid_output_attempts: u32,
) -> EffectResult {
    use crate::files::llm_output_extraction::validate_issues_xml;
    match validate_issues_xml(&issues_xml) {
        Ok(elements) => build_validation_success(pass, issues_xml, &elements),
        Err(err) => EffectResult::event(PipelineEvent::review_output_validation_failed(
            pass,
            invalid_output_attempts,
            Some(err.format_for_ai_retry()),
        )),
    }
}

fn build_validation_success(
    pass: u32,
    issues_xml: String,
    elements: &crate::files::llm_output_extraction::IssuesElements,
) -> EffectResult {
    let (issues_found, clean_no_issues, _, _) = derive_review_validation_flags(elements);
    EffectResult::with_ui(
        PipelineEvent::review_issues_xml_validated(
            pass,
            issues_found,
            clean_no_issues,
            elements.issue_texts(),
            elements.no_issues_found.clone(),
        ),
        vec![UIEvent::XmlOutput {
            xml_type: XmlOutputType::ReviewIssues,
            content: issues_xml,
            context: Some(XmlOutputContext {
                iteration: None,
                pass: Some(pass),
                snippets: Vec::new(),
            }),
        }],
    )
}

// --- materialize_xsd_retry_last_output helpers ---

fn read_xsd_retry_source(
    ctx: &PhaseContext<'_>,
    primary_path: &Path,
    archived_path: &Path,
) -> Result<String> {
    use crate::phases::review::xsd_retry_input_strategy::{
        decide_xsd_retry_input_source, XsdRetryInputSource,
    };
    let source = decide_xsd_retry_input_source(
        ctx.workspace.exists(primary_path),
        ctx.workspace.exists(archived_path),
        primary_path,
        archived_path,
    );
    match source {
        XsdRetryInputSource::Primary { ref path } => ctx.workspace.read(path).map_err(|err| {
            ErrorEvent::WorkspaceReadFailed {
                path: path.display().to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            }
            .into()
        }),
        XsdRetryInputSource::ArchivedFallback { ref path } => {
            ctx.logger
                .info("XSD retry: using archived .processed file as last output");
            ctx.workspace.read(path).map_err(|err| {
                ErrorEvent::WorkspaceReadFailed {
                    path: path.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into()
            })
        }
        XsdRetryInputSource::EmptyFallback => {
            ctx.logger.warn(
                "Missing .agent/tmp/issues.xml and .processed fallback; using empty output for review XSD retry",
            );
            Ok(String::new())
        }
    }
}

fn find_existing_xsd_signature(
    state: &crate::reducer::state::PipelineState,
    pass: u32,
    candidate: &crate::phases::review::boundary_domain::XsdRetryMaterializationSignature,
) -> Option<crate::phases::review::boundary_domain::XsdRetryMaterializationSignature> {
    use crate::phases::review::boundary_domain::XsdRetryMaterializationSignature;
    state
        .prompt_inputs
        .xsd_retry_last_output
        .as_ref()
        .filter(|m| {
            m.phase == crate::reducer::event::PipelinePhase::Review
                && m.scope_id == pass
                && m.last_output.content_id_sha256 == candidate.content_id_sha256
                && m.last_output.consumer_signature_sha256 == candidate.consumer_signature_sha256
        })
        .map(|m| XsdRetryMaterializationSignature {
            phase: m.phase,
            scope_id: m.scope_id,
            content_id_sha256: m.last_output.content_id_sha256.clone(),
            consumer_signature_sha256: m.last_output.consumer_signature_sha256.clone(),
        })
}

fn write_xsd_last_output(ctx: &PhaseContext<'_>, path: &Path, content: &str) -> Result<()> {
    ctx.workspace.write_atomic(path, content).map_err(|err| {
        ErrorEvent::WorkspaceWriteFailed {
            path: path.display().to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        }
        .into()
    })
}

fn build_xsd_last_output_input(
    candidate: &crate::phases::review::boundary_domain::XsdRetryMaterializationSignature,
    last_output_bytes: u64,
    inline_budget_bytes: u64,
    last_output_path: &Path,
) -> MaterializedPromptInput {
    MaterializedPromptInput {
        kind: PromptInputKind::LastOutput,
        content_id_sha256: candidate.content_id_sha256.clone(),
        consumer_signature_sha256: candidate.consumer_signature_sha256.clone(),
        original_bytes: last_output_bytes,
        final_bytes: last_output_bytes,
        model_budget_bytes: None,
        inline_budget_bytes: Some(inline_budget_bytes),
        representation: PromptInputRepresentation::FileReference {
            path: last_output_path.to_path_buf(),
        },
        reason: PromptMaterializationReason::PolicyForcedReference,
    }
}

fn build_xsd_materialized_events(
    input: MaterializedPromptInput,
    pass: u32,
    content_id_sha256: &str,
    last_output_bytes: u64,
    inline_budget_bytes: u64,
) -> Vec<PipelineEvent> {
    let base_event = PipelineEvent::xsd_retry_last_output_materialized(
        crate::reducer::event::PipelinePhase::Review,
        pass,
        input,
    );
    if last_output_bytes > inline_budget_bytes {
        vec![
            base_event,
            PipelineEvent::prompt_input_oversize_detected(
                crate::reducer::event::PipelinePhase::Review,
                PromptInputKind::LastOutput,
                content_id_sha256.to_string(),
                last_output_bytes,
                inline_budget_bytes,
                "xsd-retry-context".to_string(),
            ),
        ]
    } else {
        vec![base_event]
    }
}

fn extract_issue_snippets(
    issues: &[String],
    workspace: &dyn crate::workspace::Workspace,
) -> Vec<XmlCodeSnippet> {
    let requests = crate::phases::review::snippet_domain::collect_issue_snippet_requests(
        issues,
        workspace.root(),
    );

    requests
        .into_iter()
        .filter_map(|request| {
            let content = workspace.read(Path::new(&request.file)).ok()?;
            let snippet = crate::phases::review::snippet_domain::extract_snippet_lines(
                &content,
                request.start,
                request.end,
            )?;
            Some(XmlCodeSnippet {
                file: request.file,
                line_start: request.start,
                line_end: request.end,
                content: snippet,
            })
        })
        .collect()
}
