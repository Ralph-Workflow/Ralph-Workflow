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
//! The synonym tables (`DEVELOPMENT_STATUS_SYNONYMS`, `FIX_STATUS_SYNONYMS`) contain
//! only conservative, unambiguous mappings. Each mapping should only be added when
//! the intent is clearly unambiguous (e.g., "done" clearly means "completed", not "partial").

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
/// ```rust
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
}
