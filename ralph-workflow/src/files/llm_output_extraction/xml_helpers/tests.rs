//! Tests for tolerant parsing functionality.

#[cfg(test)]
mod tests {
    use super::*;

    const DEVELOPMENT_VALID_VALUES: &[&str] = &["completed", "partial", "failed"];
    const FIX_VALID_VALUES: &[&str] = &["all_issues_addressed", "issues_remain", "no_issues_found"];
    const FILE_ACTION_VALID_VALUES: &[&str] = &["create", "modify", "delete"];
    const STEP_TYPE_VALID_VALUES: &[&str] = &["file-change", "action", "research"];
    const PRIORITY_VALID_VALUES: &[&str] = &["critical", "high", "medium", "low"];
    const SEVERITY_VALID_VALUES: &[&str] = &["low", "medium", "high", "critical"];
    const LIST_TYPE_VALID_VALUES: &[&str] = &["ordered", "unordered"];

    // =========================================================================
    // Development status normalization tests
    // =========================================================================

    #[test]
    fn test_development_exact_match_completed() {
        let result = normalize_enum_value(
            "completed",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("completed".to_string()));
    }

    #[test]
    fn test_development_exact_match_partial() {
        let result = normalize_enum_value(
            "partial",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("partial".to_string()));
    }

    #[test]
    fn test_development_exact_match_failed() {
        let result = normalize_enum_value(
            "failed",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("failed".to_string()));
    }

    #[test]
    fn test_development_case_insensitive_completed() {
        let result = normalize_enum_value(
            "Completed",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("completed".to_string()));
    }

    #[test]
    fn test_development_case_insensitive_partial_upper() {
        let result = normalize_enum_value(
            "PARTIAL",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("partial".to_string()));
    }

    #[test]
    fn test_development_whitespace_trimming() {
        let result = normalize_enum_value(
            "  completed  ",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("completed".to_string()));
    }

    #[test]
    fn test_development_synonym_done_maps_to_completed() {
        let result = normalize_enum_value(
            "done",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("completed".to_string()));
    }

    #[test]
    fn test_development_synonym_success_maps_to_completed() {
        let result = normalize_enum_value(
            "success",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("completed".to_string()));
    }

    #[test]
    fn test_development_synonym_incomplete_maps_to_partial() {
        let result = normalize_enum_value(
            "incomplete",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("partial".to_string()));
    }

    #[test]
    fn test_development_synonym_in_progress_maps_to_partial() {
        let result = normalize_enum_value(
            "in_progress",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("partial".to_string()));
    }

    #[test]
    fn test_development_synonym_in_progress_hyphen_maps_to_partial() {
        let result = normalize_enum_value(
            "in-progress",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("partial".to_string()));
    }

    #[test]
    fn test_development_synonym_error_maps_to_failed() {
        let result = normalize_enum_value(
            "error",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("failed".to_string()));
    }

    #[test]
    fn test_development_synonym_failure_maps_to_failed() {
        let result = normalize_enum_value(
            "failure",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("failed".to_string()));
    }

    #[test]
    fn test_development_unknown_value_banana_returns_none() {
        let result = normalize_enum_value(
            "banana",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, None);
    }

    #[test]
    fn test_development_empty_string_returns_none() {
        let result =
            normalize_enum_value("", DEVELOPMENT_VALID_VALUES, DEVELOPMENT_STATUS_SYNONYMS);
        assert_eq!(result, None);
    }

    #[test]
    fn test_development_whitespace_only_returns_none() {
        let result =
            normalize_enum_value("   ", DEVELOPMENT_VALID_VALUES, DEVELOPMENT_STATUS_SYNONYMS);
        assert_eq!(result, None);
    }

    #[test]
    fn test_development_unknown_value_maybe_returns_none() {
        let result = normalize_enum_value(
            "maybe",
            DEVELOPMENT_VALID_VALUES,
            DEVELOPMENT_STATUS_SYNONYMS,
        );
        assert_eq!(result, None);
    }

    // =========================================================================
    // Fix status normalization tests
    // =========================================================================

    #[test]
    fn test_fix_exact_match_all_issues_addressed() {
        let result = normalize_enum_value(
            "all_issues_addressed",
            FIX_VALID_VALUES,
            FIX_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("all_issues_addressed".to_string()));
    }

    #[test]
    fn test_fix_exact_match_issues_remain() {
        let result = normalize_enum_value("issues_remain", FIX_VALID_VALUES, FIX_STATUS_SYNONYMS);
        assert_eq!(result, Some("issues_remain".to_string()));
    }

    #[test]
    fn test_fix_exact_match_no_issues_found() {
        let result = normalize_enum_value("no_issues_found", FIX_VALID_VALUES, FIX_STATUS_SYNONYMS);
        assert_eq!(result, Some("no_issues_found".to_string()));
    }

    #[test]
    fn test_fix_synonym_fixed_maps_to_all_issues_addressed() {
        let result = normalize_enum_value("fixed", FIX_VALID_VALUES, FIX_STATUS_SYNONYMS);
        assert_eq!(result, Some("all_issues_addressed".to_string()));
    }

    #[test]
    fn test_fix_synonym_addressed_maps_to_all_issues_addressed() {
        let result = normalize_enum_value("addressed", FIX_VALID_VALUES, FIX_STATUS_SYNONYMS);
        assert_eq!(result, Some("all_issues_addressed".to_string()));
    }

    #[test]
    fn test_fix_synonym_remaining_maps_to_issues_remain() {
        let result = normalize_enum_value("remaining", FIX_VALID_VALUES, FIX_STATUS_SYNONYMS);
        assert_eq!(result, Some("issues_remain".to_string()));
    }

    #[test]
    fn test_fix_synonym_none_found_maps_to_no_issues_found() {
        let result = normalize_enum_value("none_found", FIX_VALID_VALUES, FIX_STATUS_SYNONYMS);
        assert_eq!(result, Some("no_issues_found".to_string()));
    }

    #[test]
    fn test_fix_synonym_clean_maps_to_no_issues_found() {
        let result = normalize_enum_value("clean", FIX_VALID_VALUES, FIX_STATUS_SYNONYMS);
        assert_eq!(result, Some("no_issues_found".to_string()));
    }

    #[test]
    fn test_fix_unknown_value_returns_none() {
        let result = normalize_enum_value("banana", FIX_VALID_VALUES, FIX_STATUS_SYNONYMS);
        assert_eq!(result, None);
    }

    #[test]
    fn test_fix_case_insensitive_match() {
        // Case-insensitive match for canonical values
        let result = normalize_enum_value(
            "ALL_ISSUES_ADDRESSED",
            FIX_VALID_VALUES,
            FIX_STATUS_SYNONYMS,
        );
        assert_eq!(result, Some("all_issues_addressed".to_string()));
    }

    // =========================================================================
    // FileAction synonym normalization tests
    // =========================================================================

    #[test]
    fn test_file_action_exact_match_create() {
        let result = normalize_enum_value("create", FILE_ACTION_VALID_VALUES, FILE_ACTION_SYNONYMS);
        assert_eq!(result, Some("create".to_string()));
    }

    #[test]
    fn test_file_action_exact_match_modify() {
        let result = normalize_enum_value("modify", FILE_ACTION_VALID_VALUES, FILE_ACTION_SYNONYMS);
        assert_eq!(result, Some("modify".to_string()));
    }

    #[test]
    fn test_file_action_exact_match_delete() {
        let result = normalize_enum_value("delete", FILE_ACTION_VALID_VALUES, FILE_ACTION_SYNONYMS);
        assert_eq!(result, Some("delete".to_string()));
    }

    #[test]
    fn test_file_action_synonym_add_maps_to_create() {
        let result = normalize_enum_value("add", FILE_ACTION_VALID_VALUES, FILE_ACTION_SYNONYMS);
        assert_eq!(result, Some("create".to_string()));
    }

    #[test]
    fn test_file_action_synonym_new_maps_to_create() {
        let result = normalize_enum_value("new", FILE_ACTION_VALID_VALUES, FILE_ACTION_SYNONYMS);
        assert_eq!(result, Some("create".to_string()));
    }

    #[test]
    fn test_file_action_synonym_edit_maps_to_modify() {
        let result = normalize_enum_value("edit", FILE_ACTION_VALID_VALUES, FILE_ACTION_SYNONYMS);
        assert_eq!(result, Some("modify".to_string()));
    }

    #[test]
    fn test_file_action_synonym_change_maps_to_modify() {
        let result = normalize_enum_value("change", FILE_ACTION_VALID_VALUES, FILE_ACTION_SYNONYMS);
        assert_eq!(result, Some("modify".to_string()));
    }

    #[test]
    fn test_file_action_synonym_update_maps_to_modify() {
        let result = normalize_enum_value("update", FILE_ACTION_VALID_VALUES, FILE_ACTION_SYNONYMS);
        assert_eq!(result, Some("modify".to_string()));
    }

    #[test]
    fn test_file_action_synonym_remove_maps_to_delete() {
        let result = normalize_enum_value("remove", FILE_ACTION_VALID_VALUES, FILE_ACTION_SYNONYMS);
        assert_eq!(result, Some("delete".to_string()));
    }

    #[test]
    fn test_file_action_case_insensitive_add() {
        let result = normalize_enum_value("ADD", FILE_ACTION_VALID_VALUES, FILE_ACTION_SYNONYMS);
        assert_eq!(result, Some("create".to_string()));
    }

    #[test]
    fn test_file_action_case_insensitive_modify() {
        let result = normalize_enum_value("MODIFY", FILE_ACTION_VALID_VALUES, FILE_ACTION_SYNONYMS);
        assert_eq!(result, Some("modify".to_string()));
    }

    #[test]
    fn test_file_action_unknown_banana_returns_none() {
        let result = normalize_enum_value("banana", FILE_ACTION_VALID_VALUES, FILE_ACTION_SYNONYMS);
        assert_eq!(result, None);
    }

    #[test]
    fn test_file_action_empty_string_returns_none() {
        let result = normalize_enum_value("", FILE_ACTION_VALID_VALUES, FILE_ACTION_SYNONYMS);
        assert_eq!(result, None);
    }

    #[test]
    fn test_file_action_whitespace_only_returns_none() {
        let result = normalize_enum_value("   ", FILE_ACTION_VALID_VALUES, FILE_ACTION_SYNONYMS);
        assert_eq!(result, None);
    }

    // =========================================================================
    // StepType synonym normalization tests
    // =========================================================================

    #[test]
    fn test_step_type_exact_match_file_change() {
        let result =
            normalize_enum_value("file-change", STEP_TYPE_VALID_VALUES, STEP_TYPE_SYNONYMS);
        assert_eq!(result, Some("file-change".to_string()));
    }

    #[test]
    fn test_step_type_exact_match_action() {
        let result = normalize_enum_value("action", STEP_TYPE_VALID_VALUES, STEP_TYPE_SYNONYMS);
        assert_eq!(result, Some("action".to_string()));
    }

    #[test]
    fn test_step_type_exact_match_research() {
        let result = normalize_enum_value("research", STEP_TYPE_VALID_VALUES, STEP_TYPE_SYNONYMS);
        assert_eq!(result, Some("research".to_string()));
    }

    #[test]
    fn test_step_type_synonym_code_maps_to_file_change() {
        let result = normalize_enum_value("code", STEP_TYPE_VALID_VALUES, STEP_TYPE_SYNONYMS);
        assert_eq!(result, Some("file-change".to_string()));
    }

    #[test]
    fn test_step_type_synonym_code_change_maps_to_file_change() {
        let result =
            normalize_enum_value("code-change", STEP_TYPE_VALID_VALUES, STEP_TYPE_SYNONYMS);
        assert_eq!(result, Some("file-change".to_string()));
    }

    #[test]
    fn test_step_type_synonym_implementation_maps_to_file_change() {
        let result =
            normalize_enum_value("implementation", STEP_TYPE_VALID_VALUES, STEP_TYPE_SYNONYMS);
        assert_eq!(result, Some("file-change".to_string()));
    }

    #[test]
    fn test_step_type_synonym_investigate_maps_to_research() {
        let result =
            normalize_enum_value("investigate", STEP_TYPE_VALID_VALUES, STEP_TYPE_SYNONYMS);
        assert_eq!(result, Some("research".to_string()));
    }

    #[test]
    fn test_step_type_synonym_analysis_maps_to_research() {
        let result = normalize_enum_value("analysis", STEP_TYPE_VALID_VALUES, STEP_TYPE_SYNONYMS);
        assert_eq!(result, Some("research".to_string()));
    }

    #[test]
    fn test_step_type_synonym_task_maps_to_action() {
        let result = normalize_enum_value("task", STEP_TYPE_VALID_VALUES, STEP_TYPE_SYNONYMS);
        assert_eq!(result, Some("action".to_string()));
    }

    #[test]
    fn test_step_type_synonym_run_maps_to_action() {
        let result = normalize_enum_value("run", STEP_TYPE_VALID_VALUES, STEP_TYPE_SYNONYMS);
        assert_eq!(result, Some("action".to_string()));
    }

    #[test]
    fn test_step_type_synonym_execute_maps_to_action() {
        let result = normalize_enum_value("execute", STEP_TYPE_VALID_VALUES, STEP_TYPE_SYNONYMS);
        assert_eq!(result, Some("action".to_string()));
    }

    #[test]
    fn test_step_type_case_insensitive_code() {
        let result = normalize_enum_value("CODE", STEP_TYPE_VALID_VALUES, STEP_TYPE_SYNONYMS);
        assert_eq!(result, Some("file-change".to_string()));
    }

    #[test]
    fn test_step_type_unknown_returns_none() {
        let result = normalize_enum_value("banana", STEP_TYPE_VALID_VALUES, STEP_TYPE_SYNONYMS);
        assert_eq!(result, None);
    }

    #[test]
    fn test_step_type_empty_returns_none() {
        let result = normalize_enum_value("", STEP_TYPE_VALID_VALUES, STEP_TYPE_SYNONYMS);
        assert_eq!(result, None);
    }

    // =========================================================================
    // Priority synonym normalization tests
    // =========================================================================

    #[test]
    fn test_priority_exact_match_critical() {
        let result = normalize_enum_value("critical", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("critical".to_string()));
    }

    #[test]
    fn test_priority_exact_match_high() {
        let result = normalize_enum_value("high", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("high".to_string()));
    }

    #[test]
    fn test_priority_exact_match_medium() {
        let result = normalize_enum_value("medium", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("medium".to_string()));
    }

    #[test]
    fn test_priority_exact_match_low() {
        let result = normalize_enum_value("low", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("low".to_string()));
    }

    #[test]
    fn test_priority_synonym_p0_maps_to_critical() {
        let result = normalize_enum_value("p0", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("critical".to_string()));
    }

    #[test]
    fn test_priority_synonym_urgent_maps_to_critical() {
        let result = normalize_enum_value("urgent", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("critical".to_string()));
    }

    #[test]
    fn test_priority_synonym_must_maps_to_critical() {
        let result = normalize_enum_value("must", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("critical".to_string()));
    }

    #[test]
    fn test_priority_synonym_p1_maps_to_high() {
        let result = normalize_enum_value("p1", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("high".to_string()));
    }

    #[test]
    fn test_priority_synonym_important_maps_to_high() {
        let result = normalize_enum_value("important", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("high".to_string()));
    }

    #[test]
    fn test_priority_synonym_should_maps_to_high() {
        let result = normalize_enum_value("should", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("high".to_string()));
    }

    #[test]
    fn test_priority_synonym_p2_maps_to_medium() {
        let result = normalize_enum_value("p2", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("medium".to_string()));
    }

    #[test]
    fn test_priority_synonym_normal_maps_to_medium() {
        let result = normalize_enum_value("normal", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("medium".to_string()));
    }

    #[test]
    fn test_priority_synonym_p3_maps_to_low() {
        let result = normalize_enum_value("p3", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("low".to_string()));
    }

    #[test]
    fn test_priority_synonym_nice_to_have_maps_to_low() {
        let result = normalize_enum_value("nice-to-have", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("low".to_string()));
    }

    #[test]
    fn test_priority_synonym_minor_maps_to_low() {
        let result = normalize_enum_value("minor", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("low".to_string()));
    }

    #[test]
    fn test_priority_case_insensitive_p0() {
        let result = normalize_enum_value("P0", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, Some("critical".to_string()));
    }

    #[test]
    fn test_priority_unknown_returns_none() {
        let result = normalize_enum_value("banana", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, None);
    }

    #[test]
    fn test_priority_empty_returns_none() {
        let result = normalize_enum_value("", PRIORITY_VALID_VALUES, PRIORITY_SYNONYMS);
        assert_eq!(result, None);
    }

    // =========================================================================
    // Severity synonym normalization tests
    // =========================================================================

    #[test]
    fn test_severity_exact_match_low() {
        let result = normalize_enum_value("low", SEVERITY_VALID_VALUES, SEVERITY_SYNONYMS);
        assert_eq!(result, Some("low".to_string()));
    }

    #[test]
    fn test_severity_exact_match_medium() {
        let result = normalize_enum_value("medium", SEVERITY_VALID_VALUES, SEVERITY_SYNONYMS);
        assert_eq!(result, Some("medium".to_string()));
    }

    #[test]
    fn test_severity_exact_match_high() {
        let result = normalize_enum_value("high", SEVERITY_VALID_VALUES, SEVERITY_SYNONYMS);
        assert_eq!(result, Some("high".to_string()));
    }

    #[test]
    fn test_severity_exact_match_critical() {
        let result = normalize_enum_value("critical", SEVERITY_VALID_VALUES, SEVERITY_SYNONYMS);
        assert_eq!(result, Some("critical".to_string()));
    }

    #[test]
    fn test_severity_synonym_urgent_maps_to_critical() {
        let result = normalize_enum_value("urgent", SEVERITY_VALID_VALUES, SEVERITY_SYNONYMS);
        assert_eq!(result, Some("critical".to_string()));
    }

    #[test]
    fn test_severity_synonym_important_maps_to_high() {
        let result = normalize_enum_value("important", SEVERITY_VALID_VALUES, SEVERITY_SYNONYMS);
        assert_eq!(result, Some("high".to_string()));
    }

    #[test]
    fn test_severity_synonym_normal_maps_to_medium() {
        let result = normalize_enum_value("normal", SEVERITY_VALID_VALUES, SEVERITY_SYNONYMS);
        assert_eq!(result, Some("medium".to_string()));
    }

    #[test]
    fn test_severity_synonym_minor_maps_to_low() {
        let result = normalize_enum_value("minor", SEVERITY_VALID_VALUES, SEVERITY_SYNONYMS);
        assert_eq!(result, Some("low".to_string()));
    }

    #[test]
    fn test_severity_case_insensitive_urgent() {
        let result = normalize_enum_value("URGENT", SEVERITY_VALID_VALUES, SEVERITY_SYNONYMS);
        assert_eq!(result, Some("critical".to_string()));
    }

    #[test]
    fn test_severity_unknown_returns_none() {
        let result = normalize_enum_value("banana", SEVERITY_VALID_VALUES, SEVERITY_SYNONYMS);
        assert_eq!(result, None);
    }

    #[test]
    fn test_severity_empty_returns_none() {
        let result = normalize_enum_value("", SEVERITY_VALID_VALUES, SEVERITY_SYNONYMS);
        assert_eq!(result, None);
    }

    // =========================================================================
    // ListType synonym normalization tests
    // =========================================================================

    #[test]
    fn test_list_type_exact_match_ordered() {
        let result = normalize_enum_value("ordered", LIST_TYPE_VALID_VALUES, LIST_TYPE_SYNONYMS);
        assert_eq!(result, Some("ordered".to_string()));
    }

    #[test]
    fn test_list_type_exact_match_unordered() {
        let result = normalize_enum_value("unordered", LIST_TYPE_VALID_VALUES, LIST_TYPE_SYNONYMS);
        assert_eq!(result, Some("unordered".to_string()));
    }

    #[test]
    fn test_list_type_synonym_bulleted_maps_to_unordered() {
        let result = normalize_enum_value("bulleted", LIST_TYPE_VALID_VALUES, LIST_TYPE_SYNONYMS);
        assert_eq!(result, Some("unordered".to_string()));
    }

    #[test]
    fn test_list_type_synonym_bullet_maps_to_unordered() {
        let result = normalize_enum_value("bullet", LIST_TYPE_VALID_VALUES, LIST_TYPE_SYNONYMS);
        assert_eq!(result, Some("unordered".to_string()));
    }

    #[test]
    fn test_list_type_synonym_ul_maps_to_unordered() {
        let result = normalize_enum_value("ul", LIST_TYPE_VALID_VALUES, LIST_TYPE_SYNONYMS);
        assert_eq!(result, Some("unordered".to_string()));
    }

    #[test]
    fn test_list_type_synonym_numbered_maps_to_ordered() {
        let result = normalize_enum_value("numbered", LIST_TYPE_VALID_VALUES, LIST_TYPE_SYNONYMS);
        assert_eq!(result, Some("ordered".to_string()));
    }

    #[test]
    fn test_list_type_synonym_ol_maps_to_ordered() {
        let result = normalize_enum_value("ol", LIST_TYPE_VALID_VALUES, LIST_TYPE_SYNONYMS);
        assert_eq!(result, Some("ordered".to_string()));
    }

    #[test]
    fn test_list_type_case_insensitive_bulleted() {
        let result = normalize_enum_value("BULLETED", LIST_TYPE_VALID_VALUES, LIST_TYPE_SYNONYMS);
        assert_eq!(result, Some("unordered".to_string()));
    }

    #[test]
    fn test_list_type_unknown_returns_none() {
        let result = normalize_enum_value("banana", LIST_TYPE_VALID_VALUES, LIST_TYPE_SYNONYMS);
        assert_eq!(result, None);
    }

    #[test]
    fn test_list_type_empty_returns_none() {
        let result = normalize_enum_value("", LIST_TYPE_VALID_VALUES, LIST_TYPE_SYNONYMS);
        assert_eq!(result, None);
    }

    #[test]
    fn test_list_type_whitespace_only_returns_none() {
        let result = normalize_enum_value("   ", LIST_TYPE_VALID_VALUES, LIST_TYPE_SYNONYMS);
        assert_eq!(result, None);
    }

    // =========================================================================
    // Tag name fuzzy matching tests
    // =========================================================================

    const DEV_RESULT_KNOWN_TAGS: &[&str] = &[
        "ralph-status",
        "ralph-summary",
        "skills-mcp",
        "ralph-files-changed",
        "ralph-next-steps",
    ];

    #[test]
    fn test_tag_name_exact_match_returns_canonical() {
        let result = normalize_tag_name("ralph-summary", DEV_RESULT_KNOWN_TAGS);
        assert_eq!(result, Some("ralph-summary"));
    }

    #[test]
    fn test_tag_name_case_insensitive_exact_match() {
        let result = normalize_tag_name("RALPH-SUMMARY", DEV_RESULT_KNOWN_TAGS);
        assert_eq!(result, Some("ralph-summary"));
    }

    #[test]
    fn test_tag_name_single_char_deletion_typo_resolves() {
        // ralph-sumary (missing 'm') -> ralph-summary
        let result = normalize_tag_name("ralph-sumary", DEV_RESULT_KNOWN_TAGS);
        assert_eq!(result, Some("ralph-summary"));
    }

    #[test]
    fn test_tag_name_single_char_insertion_typo_resolves() {
        // ralph-ssummary (extra 's') -> ralph-summary
        let result = normalize_tag_name("ralph-ssummary", DEV_RESULT_KNOWN_TAGS);
        assert_eq!(result, Some("ralph-summary"));
    }

    #[test]
    fn test_tag_name_single_char_substitution_typo_resolves() {
        // ralph-xummary (x instead of m) -> ralph-summary
        let result = normalize_tag_name("ralph-xummary", DEV_RESULT_KNOWN_TAGS);
        assert_eq!(result, Some("ralph-summary"));
    }

    #[test]
    fn test_tag_name_completely_unknown_returns_none() {
        // ralph-banana is too far from any known tag
        let result = normalize_tag_name("ralph-banana", DEV_RESULT_KNOWN_TAGS);
        assert_eq!(result, None);
    }

    #[test]
    fn test_tag_name_empty_input_returns_none() {
        let result = normalize_tag_name("", DEV_RESULT_KNOWN_TAGS);
        assert_eq!(result, None);
    }

    #[test]
    fn test_tag_name_whitespace_only_returns_none() {
        let result = normalize_tag_name("   ", DEV_RESULT_KNOWN_TAGS);
        assert_eq!(result, None);
    }

    #[test]
    fn test_tag_name_status_typo_resolves() {
        // ralph-statuss (extra 's') -> ralph-status
        let result = normalize_tag_name("ralph-statuss", DEV_RESULT_KNOWN_TAGS);
        assert_eq!(result, Some("ralph-status"));
    }

    #[test]
    fn test_tag_name_whitespace_trimmed() {
        // " ralph-summary " should match
        let result = normalize_tag_name(" ralph-summary ", DEV_RESULT_KNOWN_TAGS);
        assert_eq!(result, Some("ralph-summary"));
    }

    #[test]
    fn test_tag_name_two_char_diff_returns_none() {
        // ralph-summray (mm->mr, remove y) is too far from any known tag
        let result = normalize_tag_name("ralph-summray", DEV_RESULT_KNOWN_TAGS);
        assert_eq!(result, None);
    }

    #[test]
    fn test_tag_name_ambiguous_input_returns_none() {
        // Case 1: input "a" is distance 1 from both "ab" and "ac" - ambiguous
        const AMBIGUOUS_TAGS_1: &[&str] = &["ab", "ac"];
        let result = normalize_tag_name("a", AMBIGUOUS_TAGS_1);
        assert_eq!(
            result, None,
            "Input 'a' is equally close to 'ab' and 'ac', should return None"
        );

        // Case 2: ralph-stytus is distance 1 from both ralph-stztus and ralph-stxtus - ambiguous
        // s->z and s->x are both single substitution at the same position
        const AMBIGUOUS_TAGS_2: &[&str] = &["ralph-stztus", "ralph-stxtus"];
        let result = normalize_tag_name("ralph-stytus", AMBIGUOUS_TAGS_2);
        assert_eq!(
            result, None,
            "Input 'ralph-stytus' is equally close to both known tags, should return None"
        );
    }

    #[test]
    fn test_tag_name_for_issues_validator() {
        const ISSUES_KNOWN_TAGS: &[&str] = &["ralph-issue", "ralph-no-issues-found"];
        // ralph-isue (typo) -> ralph-issue
        let result = normalize_tag_name("ralph-isue", ISSUES_KNOWN_TAGS);
        assert_eq!(result, Some("ralph-issue"));
    }

    #[test]
    fn test_tag_name_for_fix_result_validator() {
        const FIX_KNOWN_TAGS: &[&str] = &["ralph-status", "ralph-summary"];
        // ralph-summry (typo) -> ralph-summary
        let result = normalize_tag_name("ralph-summry", FIX_KNOWN_TAGS);
        assert_eq!(result, Some("ralph-summary"));
    }
}
