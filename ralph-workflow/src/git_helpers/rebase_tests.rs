// Unit tests for rebase operations.

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rebase_result_variants_exist() {
        // Test that RebaseResult has the expected variants
        let _ = RebaseResult::Success;
        let _ = RebaseResult::NoOp {
            reason: "test".to_string(),
        };
        let _ = RebaseResult::Conflicts(vec![]);
        let _ = RebaseResult::Failed(RebaseErrorKind::Unknown {
            details: "test".to_string(),
        });
    }

    #[test]
    fn test_rebase_result_is_noop() {
        // Test the is_noop method
        assert!(RebaseResult::NoOp {
            reason: "test".to_string()
        }
        .is_noop());
        assert!(!RebaseResult::Success.is_noop());
        assert!(!RebaseResult::Conflicts(vec![]).is_noop());
        assert!(!RebaseResult::Failed(RebaseErrorKind::Unknown {
            details: "test".to_string(),
        })
        .is_noop());
    }

    #[test]
    fn test_rebase_result_is_success() {
        // Test the is_success method
        assert!(RebaseResult::Success.is_success());
        assert!(!RebaseResult::NoOp {
            reason: "test".to_string()
        }
        .is_success());
        assert!(!RebaseResult::Conflicts(vec![]).is_success());
        assert!(!RebaseResult::Failed(RebaseErrorKind::Unknown {
            details: "test".to_string(),
        })
        .is_success());
    }

    #[test]
    fn test_rebase_result_has_conflicts() {
        // Test the has_conflicts method
        assert!(RebaseResult::Conflicts(vec!["file.txt".to_string()]).has_conflicts());
        assert!(!RebaseResult::Success.has_conflicts());
        assert!(!RebaseResult::NoOp {
            reason: "test".to_string()
        }
        .has_conflicts());
    }

    #[test]
    fn test_rebase_result_is_failed() {
        // Test the is_failed method
        assert!(RebaseResult::Failed(RebaseErrorKind::Unknown {
            details: "test".to_string(),
        })
        .is_failed());
        assert!(!RebaseResult::Success.is_failed());
        assert!(!RebaseResult::NoOp {
            reason: "test".to_string()
        }
        .is_failed());
        assert!(!RebaseResult::Conflicts(vec![]).is_failed());
    }

    #[test]
    fn test_rebase_error_kind_description() {
        // Test that error kinds produce descriptions
        let err = RebaseErrorKind::InvalidRevision {
            revision: "main".to_string(),
        };
        assert!(err.description().contains("main"));

        let err = RebaseErrorKind::DirtyWorkingTree;
        assert!(err.description().contains("Working tree"));
    }

    #[test]
    fn test_rebase_error_kind_category() {
        // Test that error kinds return correct categories
        assert_eq!(
            RebaseErrorKind::InvalidRevision {
                revision: "test".to_string()
            }
            .category(),
            1
        );
        assert_eq!(
            RebaseErrorKind::ContentConflict { files: vec![] }.category(),
            2
        );
        assert_eq!(
            RebaseErrorKind::ValidationFailed {
                reason: "test".to_string()
            }
            .category(),
            3
        );
        assert_eq!(
            RebaseErrorKind::ProcessTerminated {
                reason: "test".to_string()
            }
            .category(),
            4
        );
        assert_eq!(
            RebaseErrorKind::Unknown {
                details: "test".to_string()
            }
            .category(),
            5
        );
    }

    #[test]
    fn test_rebase_error_kind_is_recoverable() {
        // Test that error kinds correctly identify recoverable errors
        assert!(RebaseErrorKind::ConcurrentOperation {
            operation: "rebase".to_string()
        }
        .is_recoverable());
        assert!(RebaseErrorKind::ContentConflict { files: vec![] }.is_recoverable());
        assert!(!RebaseErrorKind::InvalidRevision {
            revision: "test".to_string()
        }
        .is_recoverable());
        assert!(!RebaseErrorKind::DirtyWorkingTree.is_recoverable());
    }

    #[test]
    fn test_classify_rebase_error_invalid_revision() {
        // Test classification of invalid revision errors
        let stderr = "error: invalid revision 'nonexistent'";
        let error = classify_rebase_error(stderr, "");
        assert!(matches!(error, RebaseErrorKind::InvalidRevision { .. }));
    }

    #[test]
    fn test_classify_rebase_error_conflict() {
        // Test classification of conflict errors
        let stderr = "CONFLICT (content): Merge conflict in src/main.rs";
        let error = classify_rebase_error(stderr, "");
        assert!(matches!(error, RebaseErrorKind::ContentConflict { .. }));
    }

    #[test]
    fn test_classify_rebase_error_dirty_tree() {
        // Test classification of dirty working tree errors
        let stderr = "Cannot rebase: Your index contains uncommitted changes";
        let error = classify_rebase_error(stderr, "");
        assert!(matches!(error, RebaseErrorKind::DirtyWorkingTree));
    }

    #[test]
    fn test_classify_rebase_error_concurrent_operation() {
        // Test classification of concurrent operation errors
        let stderr = "Cannot rebase: There is a rebase in progress already";
        let error = classify_rebase_error(stderr, "");
        assert!(matches!(error, RebaseErrorKind::ConcurrentOperation { .. }));
    }

    #[test]
    fn test_classify_rebase_error_unknown() {
        // Test classification of unknown errors
        let stderr = "Some completely unexpected error message";
        let error = classify_rebase_error(stderr, "");
        assert!(matches!(error, RebaseErrorKind::Unknown { .. }));
    }

}
