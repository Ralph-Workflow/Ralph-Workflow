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
//! - **Tag name fuzzy matching**: Minor typos in tag names (e.g., <ralph-sumary> instead of
//!   <ralph-summary>) are accepted if they unambiguously resolve to a known tag.
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
//! - Tag names with ambiguous fuzzy matches (multiple known tags within edit-distance threshold)
//!   return `None` and are skipped as unknown elements
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
    ("complete", "completed"),
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

    valid_values
        .iter()
        .find(|&&valid| normalized == valid)
        .map(|&valid| valid.to_string())
        .or_else(|| {
            synonyms
                .iter()
                .find(|&&(synonym, _)| normalized == synonym)
                .map(|&(_, canonical)| canonical.to_string())
        })
}

/// Compute the Levenshtein distance between two strings.
///
/// This is an inline implementation for short XML tag names (typically < 30 characters).
/// Uses dynamic programming with O(mn) time and O(min(m,n)) space.
fn levenshtein_distance(s1: &str, s2: &str) -> usize {
    let (shorter, longer) = if s1.len() <= s2.len() {
        (s1, s2)
    } else {
        (s2, s1)
    };

    let shorter_len = shorter.len();
    let longer_len = longer.len();

    if shorter_len == 0 {
        return longer_len;
    }
    if longer_len == 0 {
        return shorter_len;
    }

    let initial_row: Vec<usize> = (0..=shorter_len).collect();

    let final_row = longer
        .chars()
        .enumerate()
        .fold(initial_row, |prev_row, (i, c2)| {
            std::iter::once(i + 1)
                .chain(shorter.chars().enumerate().scan(i + 1, |last, (j, c1)| {
                    let insertion = *last + 1;
                    let deletion = prev_row[j + 1] + 1;
                    let substitution = prev_row[j] + if c1 == c2 { 0 } else { 1 };
                    let value = insertion.min(deletion).min(substitution);
                    *last = value;
                    Some(value)
                }))
                .collect()
        });

    final_row[shorter_len]
}

/// Normalize a tag name to a known tag using fuzzy matching.
///
/// This function performs tolerant matching for XML element names that may contain
/// minor typos (e.g., `<ralph-sumary>` instead of `<ralph-summary>`).
///
/// # Arguments
///
/// * `tag` - The raw tag name to normalize (may have typos)
/// * `known_tags` - The list of known/valid tag names to match against
///
/// # Returns
///
/// * `Some(&str)` - A reference to the matching known tag if exactly one tag is within
///   the edit-distance threshold (currently 1)
/// * `None` - If zero tags are within threshold OR multiple tags are equally close (ambiguous)
///
/// # Examples
///
/// ```rust,ignore
/// use ralph_workflow::files::llm_output_extraction::xml_helpers::tolerant_parsing::normalize_tag_name;
///
/// let known = &["ralph-status", "ralph-summary", "ralph-files-changed"];
///
/// // Exact match
/// assert_eq!(normalize_tag_name("ralph-summary", known), Some("ralph-summary"));
///
/// // Single char deletion typo
/// assert_eq!(normalize_tag_name("ralph-sumary", known), Some("ralph-summary"));
///
/// // Single char insertion typo
/// assert_eq!(normalize_tag_name("ralph-ssummary", known), Some("ralph-summary"));
///
/// // Completely unknown tag
/// assert_eq!(normalize_tag_name("ralph-banana", known), None);
///
/// // Ambiguous input (equally close to multiple tags)
/// assert_eq!(normalize_tag_name("ralph-status", known), None); // exact match is handled separately
/// ```
pub fn normalize_tag_name<'a>(tag: &str, known_tags: &'a [&str]) -> Option<&'a str> {
    // Step 1: Trim whitespace
    let trimmed = tag.trim();
    if trimmed.is_empty() {
        return None;
    }

    // Step 2: Check for exact case-insensitive match first (fast path)
    let lower_tag = trimmed.to_ascii_lowercase();
    if let Some(&known) = known_tags
        .iter()
        .find(|&&known| lower_tag == known.to_ascii_lowercase())
    {
        return Some(known);
    }

    // Step 3: Find all known tags within edit-distance threshold
    const DISTANCE_THRESHOLD: usize = 1;
    let matches: Vec<&str> = known_tags
        .iter()
        .copied()
        .filter(|&known| {
            levenshtein_distance(&lower_tag, &known.to_ascii_lowercase()) <= DISTANCE_THRESHOLD
        })
        .collect();

    match matches.as_slice() {
        [single] => Some(*single),
        _ => None,
    }
}

mod tests;
