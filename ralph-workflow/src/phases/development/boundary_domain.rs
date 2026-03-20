use crate::reducer::effect::ContinuationContextData;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::PipelineEvent;
use crate::reducer::state::{
    DevelopmentStatus, PromptInputRepresentation, PromptMaterializationReason,
};
use std::path::Path;

pub(crate) fn build_continuation_context_markdown(data: &ContinuationContextData) -> String {
    format!(
        "# Development Continuation Context\n\n\
- Iteration: {iteration}\n\
- Continuation attempt: {attempt}\n\
- Previous status: {status}\n\n\
## Previous summary\n\n\
{summary}\n\
{files_section}\
{steps_section}\
## Reference files (do not modify)\n\n\
- PROMPT.md\n\
- .agent/PLAN.md\n",
        iteration = data.iteration,
        attempt = data.attempt,
        status = data.status,
        summary = data.summary,
        files_section = data.files_changed.as_ref().map_or(String::new(), |files| {
            let file_list = files.iter().map(|f| format!("- {f}\n")).collect::<String>();
            format!("\n## Files changed\n\n{file_list}")
        }),
        steps_section = data
            .next_steps
            .as_ref()
            .map_or(String::new(), |steps| format!(
                "\n## Recommended next steps\n\n{steps}\n"
            )),
    )
}

pub(crate) fn select_representation_by_inline_budget(
    content_bytes: u64,
    inline_budget_bytes: u64,
    reference_path: &Path,
) -> (PromptInputRepresentation, PromptMaterializationReason) {
    if content_bytes > inline_budget_bytes {
        (
            PromptInputRepresentation::FileReference {
                path: reference_path.to_path_buf(),
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

pub(crate) fn build_development_prompt_content_id(
    mode: &str,
    prompt_content_id_sha256: &str,
    plan_content_id_sha256: &str,
    prompt_consumer_signature_sha256: &str,
    plan_consumer_signature_sha256: &str,
) -> String {
    crate::reducer::prompt_inputs::sha256_hex_str(&format!(
        "development_{mode}:prompt:{prompt_content_id_sha256}:plan:{plan_content_id_sha256}:prompt_consumer:{prompt_consumer_signature_sha256}:plan_consumer:{plan_consumer_signature_sha256}"
    ))
}

pub(crate) const fn derive_development_status(
    is_completed: bool,
    is_partial: bool,
) -> DevelopmentStatus {
    if is_completed {
        DevelopmentStatus::Completed
    } else if is_partial {
        DevelopmentStatus::Partial
    } else {
        DevelopmentStatus::Failed
    }
}

pub(crate) fn parse_files_changed_lines(files_changed: Option<&str>) -> Option<Vec<String>> {
    files_changed.map(|files| {
        files
            .lines()
            .map(std::string::ToString::to_string)
            .collect()
    })
}

pub(crate) enum PromptModeResult {
    Data(PromptModeData),
    EarlyReturn(EffectResult),
}

pub(crate) struct PromptModeData {
    pub prompt: String,
    pub template_name: &'static str,
    pub prompt_key: Option<String>,
    pub was_replayed: bool,
    pub prompt_content_id: Option<String>,
    pub rendered_log: Option<crate::prompts::SubstitutionLog>,
    pub additional_events: Vec<PipelineEvent>,
}

#[cfg(test)]
mod tests {
    use super::{
        build_continuation_context_markdown, build_development_prompt_content_id,
        derive_development_status, parse_files_changed_lines,
        select_representation_by_inline_budget,
    };
    use crate::reducer::effect::ContinuationContextData;
    use crate::reducer::state::{
        DevelopmentStatus, PromptInputRepresentation, PromptMaterializationReason,
    };
    use std::path::Path;

    #[test]
    fn build_continuation_context_markdown_renders_optional_sections() {
        let data = ContinuationContextData {
            iteration: 3,
            attempt: 2,
            status: DevelopmentStatus::Partial,
            summary: "Implemented parser.".to_string(),
            files_changed: Some(vec!["src/a.rs".to_string(), "src/b.rs".to_string()].into()),
            next_steps: Some("Wire handler".to_string()),
        };

        let content = build_continuation_context_markdown(&data);
        assert!(content.contains("- Iteration: 3"));
        assert!(content.contains("## Files changed"));
        assert!(content.contains("- src/a.rs"));
        assert!(content.contains("## Recommended next steps"));
    }

    #[test]
    fn select_representation_by_inline_budget_marks_oversize_as_file_reference() {
        let (representation, reason) = select_representation_by_inline_budget(
            20_000,
            16_000,
            Path::new(".agent/PROMPT.md.backup"),
        );

        assert_eq!(reason, PromptMaterializationReason::InlineBudgetExceeded);
        assert!(matches!(
            representation,
            PromptInputRepresentation::FileReference { .. }
        ));
    }

    #[test]
    fn build_development_prompt_content_id_is_mode_sensitive() {
        let normal = build_development_prompt_content_id("normal", "p", "l", "pc", "lc");
        let retry = build_development_prompt_content_id("same_agent_retry", "p", "l", "pc", "lc");
        assert_ne!(normal, retry);
    }

    #[test]
    fn derive_development_status_prefers_completed_then_partial() {
        assert_eq!(
            derive_development_status(true, true),
            DevelopmentStatus::Completed
        );
        assert_eq!(
            derive_development_status(false, true),
            DevelopmentStatus::Partial
        );
        assert_eq!(
            derive_development_status(false, false),
            DevelopmentStatus::Failed
        );
    }

    #[test]
    fn parse_files_changed_lines_splits_lines() {
        let parsed = parse_files_changed_lines(Some("a.rs\nb.rs\n"));
        assert_eq!(parsed, Some(vec!["a.rs".to_string(), "b.rs".to_string()]));
    }
}
