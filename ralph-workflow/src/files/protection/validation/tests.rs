// Tests for PROMPT.md validation.

// NOTE: CWD-relative filesystem tests were moved to tests/system_tests/file_protection/.

#[cfg(all(test, feature = "test-utils"))]
mod workspace_tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn test_validate_prompt_md_with_workspace_not_exists() {
        let workspace = MemoryWorkspace::new_test();

        let result = validate_prompt_md_with_workspace(&workspace, false, false);

        assert!(!result.exists());
        assert!(!result.is_valid());
        assert!(result.errors.iter().any(|e| e.contains("not found")));
    }

    #[test]
    fn test_validate_prompt_md_with_workspace_valid() {
        let workspace = MemoryWorkspace::new_test().with_file(
            "PROMPT.md",
            "# Test\n\n## Goal\nDo something\n\n## Acceptance\n- Pass",
        );

        let result = validate_prompt_md_with_workspace(&workspace, false, false);

        assert!(result.exists());
        assert!(result.has_content());
        assert!(result.has_goal);
        assert!(result.has_acceptance);
        assert!(result.is_valid());
    }

    #[test]
    fn test_validate_prompt_md_with_workspace_restores_from_backup() {
        let workspace =
            MemoryWorkspace::new_test().with_file(".agent/PROMPT.md.backup", "## Goal\nRestored");

        let result = validate_prompt_md_with_workspace(&workspace, false, false);

        // Should have restored from backup
        assert!(result.warnings.iter().any(|w| w.contains("restored from")));
        assert!(result.has_goal);
        // PROMPT.md should now exist in workspace
        assert!(workspace.exists(Path::new("PROMPT.md")));
    }

    #[test]
    fn test_validate_prompt_md_with_workspace_uses_next_backup_when_first_is_empty() {
        let workspace = MemoryWorkspace::new_test()
            .with_file(".agent/PROMPT.md.backup", "  \n")
            .with_file(".agent/PROMPT.md.backup.1", "## Goal\nRestored");

        let result = validate_prompt_md_with_workspace(&workspace, false, false);

        assert!(result.warnings.iter().any(|w| w.contains("backup.1")));
        assert!(result.has_goal);
        assert!(workspace.exists(Path::new("PROMPT.md")));
    }

    #[test]
    fn test_validate_prompt_md_with_workspace_empty() {
        let workspace = MemoryWorkspace::new_test().with_file("PROMPT.md", "   \n\n  ");

        let result = validate_prompt_md_with_workspace(&workspace, false, false);

        assert!(result.exists());
        assert!(!result.has_content());
        assert!(!result.is_valid());
        assert!(result.errors.iter().any(|e| e.contains("empty")));
    }

    #[test]
    fn test_validate_prompt_md_with_workspace_missing_sections_lenient() {
        let workspace = MemoryWorkspace::new_test().with_file("PROMPT.md", "Just some content");

        let result = validate_prompt_md_with_workspace(&workspace, false, false);

        assert!(result.is_valid()); // Lenient mode: warnings, not errors
        assert!(!result.has_goal);
        assert!(!result.has_acceptance);
        assert_eq!(result.warnings.len(), 2);
    }

    #[test]
    fn test_validate_prompt_md_with_workspace_missing_sections_strict() {
        let workspace = MemoryWorkspace::new_test().with_file("PROMPT.md", "Just some content");

        let result = validate_prompt_md_with_workspace(&workspace, true, false);

        assert!(!result.is_valid()); // Strict mode: errors
        assert_eq!(result.errors.len(), 2);
    }
}
