//! Review phase effect handlers.
//!
//! Split boundary handlers for review/fix flows.

use super::MainEffectHandler;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::review::boundary_domain::{
    derive_review_validation_flags, render_issues_markdown,
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
        use crate::files::{create_prompt_backup_with_workspace, write_diff_backup_with_workspace};

        match create_prompt_backup_with_workspace(ctx.workspace) {
            Ok(Some(warning)) => {
                ctx.logger
                    .warn(&format!("PROMPT.md backup created with warning: {warning}"));
            }
            Ok(None) => {}
            Err(err) => {
                ctx.logger
                    .warn(&format!("Failed to create PROMPT.md backup: {err}"));
            }
        }

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

        let baseline_path = Path::new(Self::DIFF_BASELINE_PATH);
        match parse_baseline_oid(&baseline_oid) {
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
        // PLAN is optional for review phase (e.g., isolation mode without planning).
        // Use sentinel content when missing and write it to PLAN.md.
        let plan_content = match ctx.workspace.read(Path::new(".agent/PLAN.md")) {
            Ok(plan_content) => plan_content,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                ctx.logger
                    .warn("Missing .agent/PLAN.md; using sentinel PLAN content for review");
                let sentinel = Self::sentinel_plan_content(ctx.config.isolation_mode);
                // Write sentinel content to PLAN.md so FileReference representation works
                let agent_dir = Path::new(".agent");
                if !ctx.workspace.exists(agent_dir) {
                    ctx.workspace.create_dir_all(agent_dir).map_err(|err| {
                        ErrorEvent::WorkspaceCreateDirAllFailed {
                            path: agent_dir.display().to_string(),
                            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                        }
                    })?;
                }
                ctx.workspace
                    .write(Path::new(".agent/PLAN.md"), &sentinel)
                    .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
                        path: ".agent/PLAN.md".to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    })?;
                sentinel
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: ".agent/PLAN.md".to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into());
            }
        };

        // DIFF is optional for review phase. Use fallback git instructions when missing.
        let baseline_oid = match ctx.workspace.read(Path::new(Self::DIFF_BASELINE_PATH)) {
            Ok(s) => s.trim().to_string(),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => String::new(),
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: Self::DIFF_BASELINE_PATH.to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into());
            }
        };

        let diff_content = match ctx.workspace.read(Path::new(".agent/DIFF.backup")) {
            Ok(diff_content) => diff_content,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                ctx.logger
                    .warn("Missing .agent/DIFF.backup; providing git diff fallback instructions");
                Self::fallback_diff_instructions(&baseline_oid)
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: ".agent/DIFF.backup".to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into());
            }
        };

        let inline_budget_bytes = MAX_INLINE_CONTENT_SIZE as u64;
        let consumer_signature_sha256 = self.state.agent_chain.consumer_signature_sha256();

        let plan_path = Path::new(".agent/PLAN.md");
        let (plan_representation, plan_reason) = if plan_content.len() as u64 > inline_budget_bytes
        {
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
        };

        let diff_path = Path::new(".agent/tmp/diff.txt");
        let (diff_representation, diff_reason) = if diff_content.len() as u64 > inline_budget_bytes
        {
            let tmp_dir = Path::new(".agent/tmp");
            if !ctx.workspace.exists(tmp_dir) {
                ctx.workspace.create_dir_all(tmp_dir).map_err(|err| {
                    ErrorEvent::WorkspaceCreateDirAllFailed {
                        path: tmp_dir.display().to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    }
                })?;
            }
            ctx.workspace
                .write_atomic(Path::new(".agent/tmp/diff.txt"), &diff_content)
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
            (
                PromptInputRepresentation::FileReference {
                    path: diff_path.to_path_buf(),
                },
                PromptMaterializationReason::InlineBudgetExceeded,
            )
        } else {
            (
                PromptInputRepresentation::Inline,
                PromptMaterializationReason::WithinBudgets,
            )
        };

        let plan_input = MaterializedPromptInput {
            kind: PromptInputKind::Plan,
            content_id_sha256: sha256_hex_str(&plan_content),
            consumer_signature_sha256: consumer_signature_sha256.clone(),
            original_bytes: plan_content.len() as u64,
            final_bytes: plan_content.len() as u64,
            model_budget_bytes: None,
            inline_budget_bytes: Some(inline_budget_bytes),
            representation: plan_representation,
            reason: plan_reason,
        };
        let diff_input = MaterializedPromptInput {
            kind: PromptInputKind::Diff,
            content_id_sha256: sha256_hex_str(&diff_content),
            consumer_signature_sha256,
            original_bytes: diff_content.len() as u64,
            final_bytes: diff_content.len() as u64,
            model_budget_bytes: None,
            inline_budget_bytes: Some(inline_budget_bytes),
            representation: diff_representation,
            reason: diff_reason,
        };

        let result = EffectResult::event(PipelineEvent::review_inputs_materialized(
            pass,
            plan_input.clone(),
            diff_input.clone(),
        ));
        let result = if plan_input.original_bytes > inline_budget_bytes {
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
                    crate::reducer::event::PipelinePhase::Review,
                    PromptInputKind::Plan,
                    plan_input.content_id_sha256.clone(),
                    plan_input.original_bytes,
                    inline_budget_bytes,
                    "inline-embedding".to_string(),
                ))
        } else {
            result
        };
        let result = if diff_input.original_bytes > inline_budget_bytes {
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
                    crate::reducer::event::PipelinePhase::Review,
                    PromptInputKind::Diff,
                    diff_input.content_id_sha256.clone(),
                    diff_input.original_bytes,
                    inline_budget_bytes,
                    "inline-embedding".to_string(),
                ))
        } else {
            result
        };
        Ok(result)
    }

    pub(super) fn invoke_review_agent(
        &mut self,
        ctx: &mut PhaseContext<'_>,
        pass: u32,
    ) -> Result<EffectResult> {
        use crate::agents::AgentRole;

        self.normalize_agent_chain_for_invocation(ctx, crate::agents::AgentDrain::Review);

        let prompt = match ctx
            .workspace
            .read(Path::new(".agent/tmp/review_prompt.txt"))
        {
            Ok(s) => s,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                return Err(ErrorEvent::ReviewPromptMissing { pass }.into());
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: ".agent/tmp/review_prompt.txt".to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into());
            }
        };

        let agent = self
            .state
            .agent_chain
            .current_agent()
            .cloned()
            .unwrap_or_else(|| ctx.reviewer_agent.to_string());

        let result = self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::Review,
            AgentRole::Reviewer,
            &agent,
            None,
            prompt,
        )?;
        let result = if result.additional_events.iter().any(|e| {
            matches!(
                e,
                PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
            )
        }) {
            {
                result
                    .clone()
                    .with_additional_event(PipelineEvent::review_agent_invoked(pass))
            }
        } else {
            result
        };
        Ok(result)
    }

    pub(super) fn extract_review_issues_xml(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> EffectResult {
        let issues_xml = Path::new(xml_paths::ISSUES_XML);
        let content = ctx.workspace.read(issues_xml);

        match content {
            Ok(_) => EffectResult::event(PipelineEvent::review_issues_xml_extracted(pass)),
            Err(err) => {
                let detail = if err.kind() == std::io::ErrorKind::NotFound {
                    None
                } else {
                    Some(format!(
                        "{:?}: {}",
                        WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                        err
                    ))
                };

                EffectResult::event(PipelineEvent::review_issues_xml_missing(
                    pass,
                    self.state.continuation.invalid_output_attempts,
                    detail,
                ))
            }
        }
    }

    pub(super) fn validate_review_issues_xml(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> EffectResult {
        use crate::files::llm_output_extraction::validate_issues_xml;

        let issues_xml = ctx.workspace.read(Path::new(xml_paths::ISSUES_XML));
        let issues_xml = match issues_xml {
            Ok(s) => s,
            Err(err) => {
                let detail = if err.kind() == std::io::ErrorKind::NotFound {
                    None
                } else {
                    Some(format!(
                        "{:?}: {}",
                        WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                        err
                    ))
                };

                return EffectResult::event(PipelineEvent::review_output_validation_failed(
                    pass,
                    self.state.continuation.invalid_output_attempts,
                    detail,
                ));
            }
        };

        match validate_issues_xml(&issues_xml) {
            Ok(elements) => {
                let (issues_found, clean_no_issues, _, _) =
                    derive_review_validation_flags(&elements);
                EffectResult::with_ui(
                    PipelineEvent::review_issues_xml_validated(
                        pass,
                        issues_found,
                        clean_no_issues,
                        elements.issue_texts(),
                        elements.no_issues_found,
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
            Err(err) => EffectResult::event(PipelineEvent::review_output_validation_failed(
                pass,
                self.state.continuation.invalid_output_attempts,
                Some(err.format_for_ai_retry()),
            )),
        }
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

    pub(super) const fn apply_review_outcome(
        _ctx: &mut PhaseContext<'_>,
        pass: u32,
        issues_found: bool,
        clean_no_issues: bool,
    ) -> EffectResult {
        if clean_no_issues {
            return EffectResult::event(PipelineEvent::review_pass_completed_clean(pass));
        }
        EffectResult::event(PipelineEvent::review_completed(pass, issues_found))
    }

    pub(super) fn materialize_xsd_retry_last_output(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> Result<Vec<PipelineEvent>> {
        use crate::phases::review::boundary_domain::XsdRetryMaterializationSignature;
        use crate::phases::review::xsd_retry_input_strategy::{
            decide_xsd_retry_input_source, XsdRetryInputSource,
        };

        // Pure domain decision: which source to use
        let primary_path = Path::new(xml_paths::ISSUES_XML);
        let archived_path = Path::new(".agent/tmp/issues.xml.processed");
        let source = decide_xsd_retry_input_source(
            ctx.workspace.exists(primary_path),
            ctx.workspace.exists(archived_path),
            primary_path,
            archived_path,
        );

        // Boundary execution: read from decided source
        let last_output = match source {
            XsdRetryInputSource::Primary { ref path } => {
                ctx.workspace
                    .read(path)
                    .map_err(|err| ErrorEvent::WorkspaceReadFailed {
                        path: path.display().to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    })?
            }
            XsdRetryInputSource::ArchivedFallback { ref path } => {
                ctx.logger
                    .info("XSD retry: using archived .processed file as last output");
                ctx.workspace
                    .read(path)
                    .map_err(|err| ErrorEvent::WorkspaceReadFailed {
                        path: path.display().to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    })?
            }
            XsdRetryInputSource::EmptyFallback => {
                ctx.logger.warn(
                    "Missing .agent/tmp/issues.xml and .processed fallback; using empty output for review XSD retry",
                );
                String::new()
            }
        };

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

        let existing = self
            .state
            .prompt_inputs
            .xsd_retry_last_output
            .as_ref()
            .filter(|m| {
                m.phase == crate::reducer::event::PipelinePhase::Review
                    && m.scope_id == pass
                    && m.last_output.content_id_sha256 == candidate.content_id_sha256
                    && m.last_output.consumer_signature_sha256
                        == candidate.consumer_signature_sha256
            })
            .map(|m| XsdRetryMaterializationSignature {
                phase: m.phase,
                scope_id: m.scope_id,
                content_id_sha256: m.last_output.content_id_sha256.clone(),
                consumer_signature_sha256: m.last_output.consumer_signature_sha256.clone(),
            });

        if should_materialize_xsd_retry_last_output(existing.as_ref(), &candidate) {
            let last_output_path = Path::new(".agent/tmp/last_output.xml");
            ctx.workspace
                .write_atomic(last_output_path, &last_output)
                .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
                    path: last_output_path.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                })?;

            let input = MaterializedPromptInput {
                kind: PromptInputKind::LastOutput,
                content_id_sha256: candidate.content_id_sha256.clone(),
                consumer_signature_sha256: candidate.consumer_signature_sha256,
                original_bytes: last_output_bytes,
                final_bytes: last_output_bytes,
                model_budget_bytes: None,
                inline_budget_bytes: Some(inline_budget_bytes),
                representation: PromptInputRepresentation::FileReference {
                    path: last_output_path.to_path_buf(),
                },
                reason: PromptMaterializationReason::PolicyForcedReference,
            };

            let base_event = PipelineEvent::xsd_retry_last_output_materialized(
                crate::reducer::event::PipelinePhase::Review,
                pass,
                input,
            );

            let events = if last_output_bytes > inline_budget_bytes {
                vec![
                    base_event,
                    PipelineEvent::prompt_input_oversize_detected(
                        crate::reducer::event::PipelinePhase::Review,
                        PromptInputKind::LastOutput,
                        content_id_sha256,
                        last_output_bytes,
                        inline_budget_bytes,
                        "xsd-retry-context".to_string(),
                    ),
                ]
            } else {
                vec![base_event]
            };

            Ok(events)
        } else {
            Ok(Vec::new())
        }
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
