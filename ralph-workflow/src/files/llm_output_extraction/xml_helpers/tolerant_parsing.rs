//! Tolerant parsing helpers for XSD validation.
//!
//! This module provides functionality for tolerant (fuzzy) parsing of XML content
//! that is semantically correct but may differ in minor structural ways from
//! the exact schema layout.
//!
//! # Tolerant Behavior
//!
//! The tolerant parsing helpers allow:
//! - **Enum normalization**: Status values that clearly correspond to expected outcomes
//!   are accepted even when using synonyms or different casing (e.g., "done" → "completed").
//! - **Unknown element skipping**: Unknown child elements are skipped instead of causing
//!   validation failure. Required elements are still enforced.
//! - **Stray text tolerance**: Whitespace or non-semantic text between elements is ignored.
//!
//! # Rejection Boundary
//!
//! Truly ambiguous or incompatible responses are still rejected:
//! - Values not in the synonym table return `None` from `normalize_enum_value`
//! - Empty values are rejected
//! - Values that could plausibly map to multiple different outcomes are not added to tables
//!
//! # Synonym Tables
//!
//! The synonym tables contain only conservative, unambiguous mappings. Each mapping
//! should only be added when the intent is clearly unambiguous (e.g., "done" clearly
//! means "completed", not "partial").

/// Synonym mappings for development result status values.
///
/// Each tuple is `(synonym, canonical_value)` where `synonym` is a non-canonical
/// value that unambiguously maps to `canonical_value`.
///
/// Canonical values: `"completed"`, `"partial"`, `"failed"`
pub const DEVELOPMENT_STATUS_SYNONYMS: &[(&str, &str)] = &[
    ("done", "completed"),
    ("success", "completed"),
    ("succeed", "completed"),
    ("succeeded", "completed"),
    ("finished", "completed"),
    ("incomplete", "partial"),
    ("in_progress", "partial"),
    ("in-progress", "partial"),
    ("error", "failed"),
    ("failure", "failed"),
];

/// Synonym mappings for fix result status values.
///
/// Each tuple is `(synonym, canonical_value)` where `synonym` is a non-canonical
/// value that unambiguously maps to `canonical_value`.
///
/// Canonical values: `"all_issues_addressed"`, `"issues_remain"`, `"no_issues_found"`
pub const FIX_STATUS_SYNONYMS: &[(&str, &str)] = &[
    ("fixed", "all_issues_addressed"),
    ("addressed", "all_issues_addressed"),
    ("all_fixed", "all_issues_addressed"),
    ("remaining", "issues_remain"),
    ("none_found", "no_issues_found"),
    ("clean", "no_issues_found"),
    ("no_issues", "no_issues_found"),
];

/// Synonym mappings for plan `FileAction` enum values.
///
/// Each tuple is `(synonym, canonical_value)` where `synonym` is a non-canonical
/// value that unambiguously maps to `canonical_value`.
///
/// Canonical values: `"create"`, `"modify"`, `"delete"`
pub const FILE_ACTION_SYNONYMS: &[(&str, &str)] = &[
    ("add", "create"),
    ("new", "create"),
    ("edit", "modify"),
    ("change", "modify"),
    ("update", "modify"),
    ("remove", "delete"),
];

/// Synonym mappings for plan `StepType` enum values.
///
/// Each tuple is `(synonym, canonical_value)` where `synonym` is a non-canonical
/// value that unambiguously maps to `canonical_value`.
///
/// Canonical values: `"file-change"`, `"action"`, `"research"`
pub const STEP_TYPE_SYNONYMS: &[(&str, &str)] = &[
    ("code", "file-change"),
    ("code-change", "file-change"),
    ("implementation", "file-change"),
    ("investigate", "research"),
    ("analysis", "research"),
    ("task", "action"),
    ("run", "action"),
    ("execute", "action"),
];

/// Synonym mappings for plan `Priority` enum values.
///
/// Each tuple is `(synonym, canonical_value)` where `synonym` is a non-canonical
/// value that unambiguously maps to `canonical_value`.
///
/// Canonical values: `"critical"`, `"high"`, `"medium"`, `"low"`
pub const PRIORITY_SYNONYMS: &[(&str, &str)] = &[
    ("p0", "critical"),
    ("urgent", "critical"),
    ("must", "critical"),
    ("p1", "high"),
    ("important", "high"),
    ("should", "high"),
    ("p2", "medium"),
    ("normal", "medium"),
    ("p3", "low"),
    ("nice-to-have", "low"),
    ("minor", "low"),
];

/// Synonym mappings for plan `Severity` enum values.
///
/// Each tuple is `(synonym, canonical_value)` where `synonym` is a non-canonical
/// value that unambiguously maps to `canonical_value`.
///
/// Canonical values: `"low"`, `"medium"`, `"high"`, `"critical"`
///
/// Note: This uses the same values as `Priority` since the enum values overlap,
/// but is a separate table for clarity and independent extensibility.
pub const SEVERITY_SYNONYMS: &[(&str, &str)] = &[
    ("p0", "critical"),
    ("urgent", "critical"),
    ("must", "critical"),
    ("p1", "high"),
    ("important", "high"),
    ("p2", "medium"),
    ("normal", "medium"),
    ("p3", "low"),
    ("minor", "low"),
];

/// Synonym mappings for plan `ListType` enum values.
///
/// Each tuple is `(synonym, canonical_value)` where `synonym` is a non-canonical
/// value that unambiguously maps to `canonical_value`.
///
/// Canonical values: `"ordered"`, `"unordered"`
pub const LIST_TYPE_SYNONYMS: &[(&str, &str)] = &[
    ("bulleted", "unordered"),
    ("bullet", "unordered"),
    ("ul", "unordered"),
    ("numbered", "ordered"),
    ("ol", "ordered"),
];

/// Normalize an enum value to its canonical form.
///
/// This function performs tolerant matching for enum-like XML content values.
/// It accepts exact canonical values, case-insensitive variations of canonical
/// values, and configured synonym mappings.
///
/// # Arguments
///
/// * `value` - The raw value to normalize (may have whitespace, different casing)
/// * `valid_values` - The canonical valid values for this enum
/// * `synonyms` - Table of `(synonym, canonical_value)` pairs for tolerant matching
///
/// # Returns
///
/// * `Some(canonical_value)` if the input can be confidently mapped to a canonical value
/// * `None` if the input is ambiguous or unknown (caller should reject)
///
/// # Examples
///
/// Internal usage example:
///
/// ```rust,ignore
/// use ralph_workflow::files::llm_output_extraction::xml_helpers::tolerant_parsing::{
///     normalize_enum_value, DEVELOPMENT_STATUS_SYNONYMS,
/// };
///
/// let valid = &["completed", "partial", "failed"];
///
/// // Exact match
/// assert_eq!(
///     normalize_enum_value("completed", valid, DEVELOPMENT_STATUS_SYNONYMS),
///     Some("completed".to_string())
/// );
///
/// // Case-insensitive
/// assert_eq!(
///     normalize_enum_value("Completed", valid, DEVELOPMENT_STATUS_SYNONYMS),
///     Some("completed".to_string())
/// );
///
/// // Synonym
/// assert_eq!(
///     normalize_enum_value("done", valid, DEVELOPMENT_STATUS_SYNONYMS),
///     Some("completed".to_string())
/// );
///
/// // Unknown value
/// assert_eq!(
///     normalize_enum_value("banana", valid, DEVELOPMENT_STATUS_SYNONYMS),
///     None
/// );
/// ```
pub fn normalize_enum_value(
    value: &str,
    valid_values: &[&str],
    synonyms: &[(&str, &str)],
) -> Option<String> {
    // Step 1: Trim whitespace and convert to lowercase for comparison
    let normalized = value.trim().to_ascii_lowercase();

    // Reject empty values
    if normalized.is_empty() {
        return None;
    }

    // Step 2: Check if it's an exact match (case-insensitive) against valid values
    for &valid in valid_values {
        if normalized == valid {
            return Some(valid.to_string());
        }
    }

    // Step 3: Check synonyms table (also case-insensitive)
    for &(synonym, canonical) in synonyms {
        if normalized == synonym {
            return Some(canonical.to_string());
        }
    }

    // Step 4: Return None - ambiguous/unknown, caller should reject
    None
}

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
}
