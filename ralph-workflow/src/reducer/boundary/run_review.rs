//! Review phase effect handlers.
//!
//! Split boundary handlers for review/fix flows.

use super::MainEffectHandler;
use crate::files::artifact_paths;
use crate::phases::review::boundary_domain::{
    derive_review_validation_flags, render_issues_markdown, review_outcome_event,
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
        match ctx.workspace.read_artifact_json("issues") {
            Ok(Some(_)) | Err(_) => {
                EffectResult::event(PipelineEvent::review_issues_xml_extracted(pass))
            }
            Ok(None) => EffectResult::event(PipelineEvent::review_issues_xml_missing(
                pass,
                self.state.continuation.invalid_output_attempts,
                None,
            )),
        }
    }

    pub(super) fn validate_review_issues_xml(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> EffectResult {
        let invalid_output_attempts = self.state.continuation.invalid_output_attempts;
        match try_validate_review_from_json(ctx, pass, invalid_output_attempts) {
            Some(result) => result,
            None => EffectResult::event(PipelineEvent::review_output_validation_failed(
                pass,
                invalid_output_attempts,
                None,
            )),
        }
    }

    pub(super) fn write_issues_markdown(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> Result<EffectResult> {
        use crate::files::result_types::{IssueEntry, IssuesElements};

        let outcome = self
            .state
            .review_validated_outcome
            .as_ref()
            .filter(|outcome| outcome.pass == pass)
            .ok_or(ErrorEvent::ValidatedReviewOutcomeMissing { pass })?;

        let elements = IssuesElements {
            issues: outcome
                .issues
                .iter()
                .map(|s| IssueEntry {
                    text: s.clone(),
                    skills_mcp: None,
                })
                .collect(),
            no_issues_found: outcome.no_issues_found.clone(),
        };

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

        let content = ctx
            .workspace
            .read_artifact_json("issues")
            .ok()
            .and_then(|opt| opt)
            .and_then(|envelope| serde_json::to_string_pretty(&envelope.content).ok())
            .unwrap_or_else(|| "[no issues artifact]".to_string());

        let snippets = extract_issue_snippets(&outcome.issues, ctx.workspace);
        Ok(EffectResult::with_ui(
            PipelineEvent::review_issue_snippets_extracted(pass),
            vec![UIEvent::XmlOutput {
                xml_type: XmlOutputType::ReviewIssues,
                content,
                context: Some(XmlOutputContext {
                    iteration: None,
                    pass: Some(pass),
                    snippets,
                }),
            }],
        ))
    }

    pub(super) fn archive_review_issues_xml(ctx: &PhaseContext<'_>, pass: u32) -> EffectResult {
        artifact_paths::archive_xml_file_with_workspace(
            ctx.workspace,
            Path::new(artifact_paths::ISSUES_XML),
        );
        crate::files::archive_json_artifact_with_workspace(ctx.workspace, "issues");
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

fn validate_review_json_envelope(
    envelope: crate::workspace::ArtifactEnvelope,
    pass: u32,
    invalid_output_attempts: u32,
) -> EffectResult {
    match super::json_artifact::issues_elements_from_envelope(&envelope) {
        Ok(elements) => {
            build_review_validation_from_elements(pass, elements, invalid_output_attempts)
        }
        Err(err) => EffectResult::event(PipelineEvent::review_output_validation_failed(
            pass,
            invalid_output_attempts,
            Some(err.to_string()),
        )),
    }
}

/// Build review validation result directly from parsed `IssuesElements` (JSON path).
///
/// Produces domain types directly from the JSON artifact without raw XML content.
fn try_validate_review_from_json(
    ctx: &PhaseContext<'_>,
    pass: u32,
    invalid_output_attempts: u32,
) -> Option<EffectResult> {
    match ctx.workspace.read_artifact_json("issues") {
        Ok(Some(envelope)) => Some(validate_review_json_envelope(
            envelope,
            pass,
            invalid_output_attempts,
        )),
        Ok(None) => None,
        Err(err) => Some(EffectResult::event(
            PipelineEvent::review_output_validation_failed(
                pass,
                invalid_output_attempts,
                Some(format!("Invalid JSON artifact 'issues': {err}")),
            ),
        )),
    }
}

fn build_review_validation_from_elements(
    pass: u32,
    elements: crate::files::result_types::IssuesElements,
    _invalid_output_attempts: u32,
) -> EffectResult {
    let (issues_found, clean_no_issues, _, _) = derive_review_validation_flags(&elements);
    // Synthesize a placeholder content string for the UI event since there is no raw XML.
    let json_note = String::from("[validated from JSON artifact]");
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
            content: json_note,
            context: Some(XmlOutputContext {
                iteration: None,
                pass: Some(pass),
                snippets: Vec::new(),
            }),
        }],
    )
}

include!("run_review_helpers.rs");
