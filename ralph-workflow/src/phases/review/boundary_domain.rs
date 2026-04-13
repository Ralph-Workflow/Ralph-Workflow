use crate::files::result_types::IssuesElements;
use crate::reducer::domain::baseline::BaselineOid;
use crate::reducer::event::PipelineEvent;
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::rendering::xml::render_skills_mcp_markdown;
// Regex lazy-init uses OnceLock — lives in boundary submodule.
include!("boundary_domain/io.rs");

pub(crate) fn sentinel_plan_content(_isolation_mode: bool) -> String {
    if _isolation_mode {
        "No PLAN provided (normal in isolation mode)".to_string()
    } else {
        "No PLAN provided".to_string()
    }
}

pub(crate) fn fallback_diff_instructions(baseline_oid: Option<&BaselineOid>) -> String {
    if let Some(baseline) = baseline_oid {
        format!(
            "[DIFF NOT AVAILABLE - Use git to obtain changes]\n\n\
             1) Committed changes since baseline:\n\
                git diff {}..HEAD\n\n\
             2) Include staged + unstaged working tree changes vs baseline:\n\
                git diff {}\n\n\
             3) Staged-only changes vs baseline:\n\
                git diff --cached {}\n\n\
             4) Untracked files (not shown by git diff):\n\
                git ls-files --others --exclude-standard\n\n\
             Review the full change set (committed + working tree + untracked).",
            baseline.as_str(),
            baseline.as_str(),
            baseline.as_str(),
        )
    } else {
        "[DIFF NOT AVAILABLE - Use git to obtain changes]\n\n\
         Run: git diff HEAD~1..HEAD  # Changes in last commit\n\
         Or:  git diff --staged      # Staged changes\n\
         Or:  git diff               # Unstaged changes\n\
         And: git ls-files --others --exclude-standard  # Untracked files\n\n\
         Review the diff and identify any issues."
            .to_string()
    }
}

pub(crate) fn render_issues_markdown(_elements: &IssuesElements) -> String {
    if let Some(message) = &_elements.no_issues_found {
        let trimmed = message.trim();
        return if trimmed.is_empty() {
            "# Issues\n\nNo issues found.\n".to_string()
        } else {
            format!("# Issues\n\n{}\n", trimmed)
        };
    }

    if _elements.issues.is_empty() {
        return "# Issues\n\nNo issues found.\n".to_string();
    }

    let issues_text = _elements
        .issues
        .iter()
        .filter_map(|issue| {
            let trimmed = issue.text.trim();
            if trimmed.is_empty() {
                None
            } else {
                let skills_mcp_text = render_skills_mcp_markdown(issue.skills_mcp.as_ref());
                Some(format!("- [ ] {}{}", trimmed, skills_mcp_text))
            }
        })
        .collect::<Vec<_>>()
        .join("\n");

    format!("# Issues\n\n{}", issues_text)
}

pub(crate) fn derive_review_validation_flags(
    _elements: &IssuesElements,
) -> (bool, bool, Vec<String>, Option<String>) {
    let issues_found = !_elements.issues.is_empty();
    let clean_no_issues = _elements.no_issues_found.is_some() && _elements.issues.is_empty();
    (
        issues_found,
        clean_no_issues,
        _elements.issue_texts(),
        _elements.no_issues_found.clone(),
    )
}

pub(crate) fn build_review_prompt_content_id(
    _mode: &str,
    _plan_content_id: &str,
    _diff_content_id: &str,
    _baseline_oid: &str,
    _consumer_signature_sha256: &str,
) -> String {
    sha256_hex_str(&format!(
        "{}|plan:{}|diff:{}|baseline:{}|consumer:{}",
        _mode, _plan_content_id, _diff_content_id, _baseline_oid, _consumer_signature_sha256,
    ))
}

/// Map review outcome flags to the appropriate pipeline event.
///
/// The orchestrator computes `issues_found` and `clean_no_issues` from the
/// parsed review output; this pure function encodes the domain rule for which
/// event each outcome produces.
pub(crate) fn review_outcome_event(
    pass: u32,
    issues_found: bool,
    clean_no_issues: bool,
) -> PipelineEvent {
    if clean_no_issues {
        PipelineEvent::review_pass_completed_clean(pass)
    } else {
        PipelineEvent::review_completed(pass, issues_found)
    }
}

pub(crate) fn build_fix_prompt_content_id(
    _prompt_id: &str,
    _plan_id: &str,
    _issues_id: &str,
    _same_agent_retry_count: u32,
) -> String {
    sha256_hex_str(&format!(
        "fix_same_agent_retry|count:{}|{}|{}|{}",
        _same_agent_retry_count, _prompt_id, _plan_id, _issues_id
    ))
}

pub(crate) fn build_fix_normal_prompt_content_id(
    _prompt_id: &str,
    _plan_id: &str,
    _issues_id: &str,
) -> String {
    sha256_hex_str(&format!(
        "fix_xml|{}|{}|{}",
        _prompt_id, _plan_id, _issues_id
    ))
}

pub(crate) fn build_fix_continuation_prompt_content_id(
    _attempt: u32,
    _status: &str,
    _summary: &str,
    _prompt_id: &str,
    _plan_id: &str,
    _issues_id: &str,
) -> String {
    sha256_hex_str(&format!(
        "fix_continuation|attempt:{}|status:{}|summary:{}|{}|{}|{}",
        _attempt, _status, _summary, _prompt_id, _plan_id, _issues_id
    ))
}

pub(crate) fn render_fix_continuation_note(
    _fix_continuation_attempt: u32,
    _max_fix_continue_count: u32,
    _status: &str,
    _summary: &str,
) -> String {
    format!(
        "## Fix Continuation\n\nThis is continuation attempt {} of {}.\nPrevious status: {}\nPrevious summary: {}\n\nContinue from the prior fix attempt instead of starting over. Preserve completed work and focus on unresolved review issues before writing the next XML result.\n",
        _fix_continuation_attempt, _max_fix_continue_count, _status, _summary
    )
}

pub(crate) fn parse_development_result_status(_status: &str) -> crate::reducer::state::FixStatus {
    match _status {
        "completed" => crate::reducer::state::FixStatus::AllIssuesAddressed,
        _ => crate::reducer::state::FixStatus::IssuesRemain,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::files::result_types::{IssueEntry, SkillsMcp};
    use crate::reducer::domain::baseline::parse_baseline_oid;

    #[test]
    fn sentinel_plan_content_uses_isolation_hint_when_enabled() {
        assert_eq!(
            sentinel_plan_content(true),
            "No PLAN provided (normal in isolation mode)"
        );
        assert_eq!(sentinel_plan_content(false), "No PLAN provided");
    }

    #[test]
    fn fallback_diff_instructions_include_baseline_when_available() {
        let baseline = parse_baseline_oid("abc123").expect("baseline should parse");
        let content = fallback_diff_instructions(Some(&baseline));
        assert!(content.contains("git diff abc123..HEAD"));
        assert!(content.contains("git diff --cached abc123"));
    }

    #[test]
    fn fallback_diff_instructions_omits_baseline_steps_when_missing() {
        let content = fallback_diff_instructions(None);
        assert!(content.contains("Run: git diff HEAD~1..HEAD"));
        assert!(!content.contains("git diff --cached"));
    }

    #[test]
    fn issue_location_regex_matches_standard_and_github_locations() {
        let standard = "src/lib.rs:10-20";
        let capture = issue_location_regex()
            .captures(standard)
            .expect("standard location should parse");
        assert_eq!(capture.name("file").map(|m| m.as_str()), Some("src/lib.rs"));

        let gh = "src/lib.rs#L4-L8";
        let gh_capture = issue_gh_location_regex()
            .captures(gh)
            .expect("github location should parse");
        assert_eq!(gh_capture.name("start").map(|m| m.as_str()), Some("4"));
        assert_eq!(gh_capture.name("end").map(|m| m.as_str()), Some("8"));
    }

    #[test]
    fn render_issues_markdown_renders_checklist_and_skills_mcp() {
        let elements = IssuesElements {
            issues: vec![IssueEntry {
                text: "src/lib.rs:42 - Example".to_string(),
                skills_mcp: Some(SkillsMcp {
                    skills: vec![crate::files::result_types::SkillEntry {
                        name: "test-driven-development".to_string(),
                        reason: Some("write failing test first".to_string()),
                    }],
                    mcps: vec![],
                    raw_content: None,
                }),
            }],
            no_issues_found: None,
        };

        let markdown = render_issues_markdown(&elements);
        assert!(markdown.starts_with("# Issues"));
        assert!(markdown.contains("- [ ] src/lib.rs:42 - Example"));
        assert!(markdown.contains("test-driven-development"));
    }

    #[test]
    fn review_outcome_event_emits_clean_event_when_no_issues() {
        let event = review_outcome_event(2, false, true);
        assert!(
            matches!(
                event,
                PipelineEvent::Review(crate::reducer::event::ReviewEvent::PassCompletedClean {
                    pass: 2
                })
            ),
            "clean_no_issues=true must produce PassCompletedClean, got {event:?}"
        );
    }

    #[test]
    fn review_outcome_event_emits_completed_with_issues_found_flag() {
        let event = review_outcome_event(3, true, false);
        assert!(
            matches!(
                event,
                PipelineEvent::Review(crate::reducer::event::ReviewEvent::Completed {
                    pass: 3,
                    issues_found: true
                })
            ),
            "issues_found=true must produce Completed with issues_found=true, got {event:?}"
        );
    }

    #[test]
    fn review_outcome_event_emits_completed_when_issues_found_false_and_not_clean() {
        let event = review_outcome_event(1, false, false);
        assert!(
            matches!(
                event,
                PipelineEvent::Review(crate::reducer::event::ReviewEvent::Completed {
                    pass: 1,
                    issues_found: false
                })
            ),
            "clean_no_issues=false must produce Completed (with issues_found=false), got {event:?}"
        );
    }

    #[test]
    fn derive_review_validation_flags_tracks_clean_vs_issue_outcomes() {
        let no_issues = IssuesElements {
            issues: vec![],
            no_issues_found: Some("No issues found".to_string()),
        };
        let (issues_found, clean_no_issues, issue_texts, no_issues_text) =
            derive_review_validation_flags(&no_issues);
        assert!(!issues_found);
        assert!(clean_no_issues);
        assert!(issue_texts.is_empty());
        assert_eq!(no_issues_text.as_deref(), Some("No issues found"));
    }

    #[test]
    fn build_review_prompt_content_id_is_mode_sensitive() {
        let normal = build_review_prompt_content_id("review_normal", "a", "b", "base", "sig");
        let retry =
            build_review_prompt_content_id("review_same_agent_retry", "a", "b", "base", "sig");
        assert_ne!(normal, retry);
    }

    #[test]
    fn build_fix_prompt_content_id_changes_with_retry_count() {
        let one = build_fix_prompt_content_id("prompt", "plan", "issues", 1);
        let two = build_fix_prompt_content_id("prompt", "plan", "issues", 2);
        assert_ne!(one, two);
    }

    #[test]
    fn build_fix_normal_prompt_content_id_is_stable_for_same_inputs() {
        let one = build_fix_normal_prompt_content_id("prompt", "plan", "issues");
        let two = build_fix_normal_prompt_content_id("prompt", "plan", "issues");
        assert_eq!(one, two);
    }

    #[test]
    fn build_fix_continuation_prompt_content_id_includes_status_and_summary() {
        let first = build_fix_continuation_prompt_content_id(
            2,
            "issues_remain",
            "continue",
            "prompt",
            "plan",
            "issues",
        );
        let second = build_fix_continuation_prompt_content_id(
            2,
            "all_issues_addressed",
            "continue",
            "prompt",
            "plan",
            "issues",
        );
        assert_ne!(first, second);
    }

    #[test]
    fn render_fix_continuation_note_contains_attempt_and_summary() {
        let note = render_fix_continuation_note(2, 4, "issues_remain", "Keep going");
        assert!(note.contains("continuation attempt 2 of 4"));
        assert!(note.contains("Previous summary: Keep going"));
    }

    #[test]
    fn parse_development_result_status_maps_completed() {
        let status = parse_development_result_status("completed");
        assert_eq!(status, crate::reducer::state::FixStatus::AllIssuesAddressed);
    }
}
