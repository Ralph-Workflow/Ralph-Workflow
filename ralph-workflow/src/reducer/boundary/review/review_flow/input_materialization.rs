// Review phase input materialization.
//
// This module handles reading, validating, and preparing input files (PLAN.md and DIFF.backup)
// for the review phase. When files are missing, it provides sentinel content or fallback
// instructions to ensure the review can proceed.
//
// ## Responsibilities
//
// - Reading PLAN.md with sentinel content for isolation mode
// - Reading DIFF.backup with fallback git instructions
// - Computing SHA256 content IDs for change detection
// - Deciding inline vs FileReference representation based on size budgets
// - Creating MaterializedPromptInput structs
// - Calling git helpers to compute diffs
// - Writing backup files (.agent/DIFF.backup, .agent/DIFF.base)
// - Emitting materialization and oversize detection events
//
// ## Isolation Mode
//
// When `developer_iters=0` and `reviewer_reviews>0`, planning does not occur.
// In this case, PLAN.md may be missing and sentinel content is used.

impl MainEffectHandler {
    /// Sentinel content for missing PLAN during review phase.
    ///
    /// This is used when `.agent/PLAN.md` is missing, which can happen in isolation mode
    /// (`developer_iters=0`, `reviewer_reviews>0`) where no planning occurred.
    pub(in crate::reducer::boundary) fn sentinel_plan_content(isolation_mode: bool) -> String {
        if isolation_mode {
            "No PLAN provided (normal in isolation mode)".to_string()
        } else {
            "No PLAN provided".to_string()
        }
    }

    /// Fallback diff instructions when `.agent/DIFF.backup` is missing.
    ///
    /// These instructions tell the reviewer how to obtain the diff via git commands.
    pub(in crate::reducer::boundary) fn fallback_diff_instructions(baseline_oid: &str) -> String {
        if baseline_oid.is_empty() {
            "[DIFF NOT AVAILABLE - Use git to obtain changes]\n\n\
             Run: git diff HEAD~1..HEAD  # Changes in last commit\n\
             Or:  git diff --staged      # Staged changes\n\
             Or:  git diff               # Unstaged changes\n\
             And: git ls-files --others --exclude-standard  # Untracked files\n\n\
             Review the diff and identify any issues."
                .to_string()
        } else {
            format!(
                "[DIFF NOT AVAILABLE - Use git to obtain changes]\n\n\
                 1) Committed changes since baseline:\n\
                    git diff {baseline_oid}..HEAD\n\n\
                 2) Include staged + unstaged working tree changes vs baseline:\n\
                    git diff {baseline_oid}\n\n\
                 3) Staged-only changes vs baseline:\n\
                    git diff --cached {baseline_oid}\n\n\
                 4) Untracked files (not shown by git diff):\n\
                    git ls-files --others --exclude-standard\n\n\
                 Review the full change set (committed + working tree + untracked)."
            )
        }
    }

    pub(in crate::reducer::boundary) fn materialize_review_inputs(
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

    pub(in crate::reducer::boundary) fn prepare_review_context(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> EffectResult {
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
        if baseline_oid.trim().is_empty() {
            let _ = ctx.workspace.remove_if_exists(baseline_path);
        } else if let Err(err) = ctx.workspace.write(baseline_path, &baseline_oid) {
            ctx.logger
                .warn(&format!("Failed to write review diff baseline: {err}"));
        }

        EffectResult::with_ui(
            PipelineEvent::review_context_prepared(pass),
            vec![UIEvent::ReviewProgress {
                pass,
                total: self.state.total_reviewer_passes,
            }],
        )
    }
}
