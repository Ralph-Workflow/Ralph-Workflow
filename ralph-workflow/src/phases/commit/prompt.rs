pub(crate) fn commit_xsd_retry_prompt_content_id(
    diff_content_id: &str,
    xsd_error: &str,
    consumer_sig: &str,
) -> String {
    crate::reducer::prompt_inputs::sha256_hex_str(&format!(
        "commit_xsd_retry|diff:{}|xsd_error:{}|consumer:{}",
        diff_content_id, xsd_error, consumer_sig,
    ))
}

pub(crate) fn commit_prompt_content_id(
    diff_content_id: &str,
    consumer_sig: &str,
    residual_files: &[String],
) -> String {
    let residual_files_key = if residual_files.is_empty() {
        String::new()
    } else {
        format!("|residual:{}", residual_files.join(","))
    };

    crate::reducer::prompt_inputs::sha256_hex_str(&format!(
        "commit_prompt|diff:{diff_content_id}|consumer:{consumer_sig}{residual_files_key}"
    ))
}

pub(crate) fn prepend_residual_files_context(
    base_prompt: &str,
    residual_files: &[String],
) -> String {
    if residual_files.is_empty() {
        return base_prompt.to_string();
    }

    let file_list = residual_files
        .iter()
        .map(|f| format!("  - {f}"))
        .collect::<Vec<_>>()
        .join("\n");

    format!(
        "NOTE: The following files were carried forward from a \
         previous commit pass and must be accounted for in this commit run:\n\
         {file_list}\n\n\
         If you do not include a file above in `<ralph-files>`, you must list it in \
         `<ralph-excluded-files>` with an explicit `reason` (e.g., `internal-ignore`, \
         `not-task-related`, `sensitive`, `deferred`).\n\n{}",
        base_prompt
    )
}

pub(crate) fn diff_unavailable_investigation_instructions(err: &str) -> String {
    format!(
        r#"## DIFF UNAVAILABLE - INVESTIGATION REQUIRED

The `git diff` command failed with error: {err}

You must investigate what changed by:

1. Run `git status` to see which files are modified/staged
2. Examine the content of modified files to understand what changed
3. Compare with recent git history if available (`git log -1 --stat`)
4. Based on your investigation, generate an appropriate commit message

If you determine there are NO actual changes to commit, respond with:
<ralph-commit><ralph-skip>Your reason why no commit is needed</ralph-skip></ralph-commit>

Example skip reasons:
- "No staged changes found via git status"
- "All changes were already committed"
- "Only whitespace or formatting changes that should not be committed"
"#
    )
}

pub(crate) fn base_prompt_for_same_agent_retry(
    previous_prompt: Option<&str>,
    generated_base_prompt: &str,
) -> (String, bool) {
    previous_prompt.map_or_else(
        || (generated_base_prompt.to_string(), true),
        |prompt| {
            (
                crate::reducer::boundary::retry_guidance::strip_existing_same_agent_retry_preamble(
                    prompt,
                )
                .to_string(),
                false,
            )
        },
    )
}

pub(crate) fn prompt_captured_event(
    prompt_key: &str,
    prompt: &str,
    prompt_content_id: &str,
    was_replayed: bool,
) -> Option<crate::reducer::event::PipelineEvent> {
    if was_replayed {
        None
    } else {
        Some(crate::reducer::event::PipelineEvent::PromptInput(
            crate::reducer::event::PromptInputEvent::PromptCaptured {
                key: prompt_key.to_string(),
                content: prompt.to_string(),
                content_id: Some(prompt_content_id.to_string()),
            },
        ))
    }
}

pub(crate) fn commit_prompt_prepared_result(
    attempt: u32,
    from_phase: crate::reducer::event::PipelinePhase,
    prompt_key: String,
    was_replayed: bool,
    prompt_captured_event: Option<crate::reducer::event::PipelineEvent>,
    rendered_log: Option<crate::prompts::SubstitutionLog>,
    template_name: &str,
) -> crate::reducer::effect::EffectResult {
    crate::reducer::effect::EffectResult::event(
        crate::reducer::event::PipelineEvent::commit_prompt_prepared(attempt),
    )
    .with_ui_event(crate::reducer::ui_event::UIEvent::PhaseTransition {
        from: Some(from_phase),
        to: crate::reducer::event::PipelinePhase::CommitMessage,
    })
    .with_ui_event(crate::reducer::ui_event::UIEvent::PromptReplayHit {
        key: prompt_key,
        was_replayed,
    })
    .maybe_with_additional_event(prompt_captured_event)
    .maybe_with_additional_event(rendered_log.map(|log| {
        crate::reducer::event::PipelineEvent::template_rendered(
            crate::reducer::event::PipelinePhase::CommitMessage,
            template_name.to_string(),
            log,
        )
    }))
}

pub(crate) fn commit_representation_and_reason(
    final_bytes: u64,
    inline_budget_bytes: u64,
    truncated_for_model_budget: bool,
    model_safe_path: &std::path::Path,
) -> (
    crate::reducer::state::PromptInputRepresentation,
    crate::reducer::state::PromptMaterializationReason,
) {
    let representation = if final_bytes <= inline_budget_bytes {
        crate::reducer::state::PromptInputRepresentation::Inline
    } else {
        crate::reducer::state::PromptInputRepresentation::FileReference {
            path: model_safe_path.to_path_buf(),
        }
    };

    let reason = if truncated_for_model_budget {
        crate::reducer::state::PromptMaterializationReason::ModelBudgetExceeded
    } else if matches!(
        representation,
        crate::reducer::state::PromptInputRepresentation::FileReference { .. }
    ) {
        crate::reducer::state::PromptMaterializationReason::InlineBudgetExceeded
    } else {
        crate::reducer::state::PromptMaterializationReason::WithinBudgets
    };

    (representation, reason)
}

fn build_commit_prompt(
    template_context: &TemplateContext,
    working_diff: &str,
    workspace: &dyn Workspace,
) -> (String, crate::prompts::SubstitutionLog) {
    let (capabilities, policy_flags) = crate::prompts::SessionCapabilities::from_drain(
        crate::agents::session::SessionDrain::Commit,
    );
    let session_caps = crate::prompts::SessionCapabilities::new(&capabilities, &policy_flags);
    let rendered = crate::prompts::prompt_generate_commit_message_with_diff_with_log(
        template_context,
        working_diff,
        workspace,
        "commit_message_xml",
        session_caps,
    );
    (rendered.content, rendered.log)
}

fn stderr_contains_auth_error(stderr: &str) -> bool {
    let lower = stderr.to_lowercase();
    lower.contains("authentication")
        || lower.contains("api key")
        || lower.contains("invalid key")
        || lower.contains("unauthorized")
        || lower.contains("permission denied")
}

fn commit_submission_retry_prompt(base_prompt: &str, submit_tool_name: &str) -> String {
    format!(
        "{base_prompt}\n\n## Submission Retry (MANDATORY)\n\
You already analyzed the diff and produced the commit payload, but Ralph did not receive a submitted artifact.\n\
Do NOT print the commit message or JSON to stdout again.\n\
Do NOT summarize your answer in plain text.\n\
Do NOT run git commit or any repository-writing command yourself; commit execution is orchestrator-owned.\n\
Call `{submit_tool_name}` now to submit the commit artifact.\n\
Call `{submit_tool_name}` now to submit the artifact.\n\
If you already generated the JSON/content, reuse it exactly and submit it now.\n\
Only if the tool is genuinely unavailable after trying should you explain that specific tool failure."
    )
}
