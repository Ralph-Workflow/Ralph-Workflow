// =========================================================================
// FixStatus tests
// =========================================================================

#[test]
fn test_fix_status_parse() {
    assert_eq!(
        FixStatus::parse("all_issues_addressed"),
        Some(FixStatus::AllIssuesAddressed)
    );
    assert_eq!(
        FixStatus::parse("issues_remain"),
        Some(FixStatus::IssuesRemain)
    );
    assert_eq!(
        FixStatus::parse("no_issues_found"),
        Some(FixStatus::NoIssuesFound)
    );
    assert_eq!(FixStatus::parse("failed"), Some(FixStatus::Failed));
    assert_eq!(FixStatus::parse("unknown"), None);
}

#[test]
fn test_fix_status_display() {
    assert_eq!(
        format!("{}", FixStatus::AllIssuesAddressed),
        "all_issues_addressed"
    );
    assert_eq!(format!("{}", FixStatus::IssuesRemain), "issues_remain");
    assert_eq!(format!("{}", FixStatus::NoIssuesFound), "no_issues_found");
    assert_eq!(format!("{}", FixStatus::Failed), "failed");
}

#[test]
fn test_fix_status_is_complete() {
    assert!(FixStatus::AllIssuesAddressed.is_complete());
    assert!(FixStatus::NoIssuesFound.is_complete());
    assert!(!FixStatus::IssuesRemain.is_complete());
    assert!(!FixStatus::Failed.is_complete());
}

#[test]
fn test_fix_status_needs_continuation() {
    assert!(!FixStatus::AllIssuesAddressed.needs_continuation());
    assert!(!FixStatus::NoIssuesFound.needs_continuation());
    assert!(FixStatus::IssuesRemain.needs_continuation());
    assert!(
        FixStatus::Failed.needs_continuation(),
        "Failed status should trigger continuation like IssuesRemain"
    );
}
