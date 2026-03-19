use super::*;

fn strip_prompt_archive_sequence(filename: &str) -> String {
    let without_ext = filename
        .strip_suffix(".txt")
        .expect("archive filename should end with .txt");
    let parts: Vec<&str> = without_ext.split('_').collect();
    let n = parts.len();
    assert!(n >= 3, "unexpected archive filename shape: {filename}");

    let timestamp = parts[n - 1];
    let seq = parts[n - 2];
    assert!(
        seq.starts_with('s') && seq[1..].chars().all(|c| c.is_ascii_digit()),
        "expected sequence segment like s123, got '{seq}' in '{filename}'"
    );

    format!(
        "{}.txt",
        parts[..n - 2]
            .iter()
            .copied()
            .chain(std::iter::once(timestamp))
            .collect::<Vec<_>>()
            .join("_")
    )
}

#[test]
fn test_build_prompt_archive_filename_is_unique_across_calls_with_same_timestamp() {
    let a = build_prompt_archive_filename(
        "planning",
        "codex",
        ".agent/logs/planning_1",
        Some(0),
        Some(0),
        123,
    );
    let b = build_prompt_archive_filename(
        "planning",
        "codex",
        ".agent/logs/planning_1",
        Some(0),
        Some(0),
        123,
    );

    assert_ne!(a, b);
    assert!(a.ends_with("_123.txt"));
    assert!(b.ends_with("_123.txt"));
}

#[test]
fn test_build_prompt_archive_filename_from_structured_log_components() {
    let name = build_prompt_archive_filename(
        "planning",
        "ccs/glm",
        ".agent/logs/planning_1",
        Some(0),
        Some(2),
        123,
    );
    assert_eq!(
        strip_prompt_archive_sequence(&name),
        "planning_1_ccs-glm_0_a2_123.txt"
    );
    assert!(!name.contains(".log"));
}

#[test]
fn test_build_prompt_archive_filename_for_review_logs_without_agent_in_name() {
    let name = build_prompt_archive_filename(
        "review",
        "codex",
        ".agent/logs/reviewer_review_2",
        None,
        None,
        42,
    );
    assert_eq!(
        strip_prompt_archive_sequence(&name),
        "reviewer_review_2_codex_42.txt"
    );
}

#[test]
fn test_build_prompt_archive_filename_dedupes_when_logfile_is_agent_only() {
    let name = build_prompt_archive_filename("dev", "claude", ".agent/logs/claude", None, None, 7);
    assert_eq!(strip_prompt_archive_sequence(&name), "dev_claude_7.txt");
}

#[test]
fn test_build_prompt_archive_filename_does_not_depend_on_logfile_stem_parsing() {
    // Agent names may contain underscores. The archive filename should remain stable
    // and should not attempt to reverse-parse delimiters from the logfile stem.
    let name = build_prompt_archive_filename(
        "planning",
        "openai/gpt_4o",
        ".agent/logs/planning_1",
        Some(0),
        Some(2),
        123,
    );
    assert_eq!(
        strip_prompt_archive_sequence(&name),
        "planning_1_openai-gpt_4o_0_a2_123.txt"
    );
}
