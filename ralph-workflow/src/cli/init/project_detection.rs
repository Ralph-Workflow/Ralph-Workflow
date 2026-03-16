// Project type detection and fuzzy matching logic.
//
// This file is included via include!() macro from the parent init.rs module.
// Contains Levenshtein distance calculation and template name fuzzy matching.

/// Calculate Levenshtein distance between two strings.
///
/// Returns the minimum number of single-character edits (insertions, deletions,
/// or substitutions) required to change one string into the other.
fn levenshtein_distance(a: &str, b: &str) -> usize {
    let a_chars: Vec<char> = a.chars().collect();
    let b_chars: Vec<char> = b.chars().collect();
    let b_len = b_chars.len();

    // Use two rows to save memory
    let mut prev_row: Vec<usize> = (0..=b_len).collect();
    let mut curr_row = vec![0; b_len + 1];

    for (i, a_char) in a_chars.iter().enumerate() {
        curr_row[0] = i + 1;

        for (j, b_char) in b_chars.iter().enumerate() {
            let cost = usize::from(a_char != b_char);
            curr_row[j + 1] = std::cmp::min(
                std::cmp::min(
                    curr_row[j] + 1,     // deletion
                    prev_row[j + 1] + 1, // insertion
                ),
                prev_row[j] + cost, // substitution
            );
        }

        std::mem::swap(&mut prev_row, &mut curr_row);
    }

    prev_row[b_len]
}

/// Calculate similarity score as a percentage (0-100).
///
/// This avoids floating point comparison issues in tests.
fn similarity_percentage(a: &str, b: &str) -> u32 {
    if a == b {
        return 100;
    }
    if a.is_empty() || b.is_empty() {
        return 0;
    }

    let max_len = a.len().max(b.len());
    let distance = levenshtein_distance(a, b);

    if max_len == 0 {
        return 100;
    }

    // Calculate percentage without floating point
    // (100 * (max_len - distance)) / max_len
    let diff = max_len.saturating_sub(distance);
    // The division result is guaranteed to fit in u32 since it's <= 100
    u32::try_from((100 * diff) / max_len).unwrap_or(0)
}

/// Find the best matching template names using fuzzy matching.
///
/// Returns templates that are similar to the input within the threshold.
pub fn find_similar_templates(input: &str) -> Vec<(&'static str, u32)> {
    let input_lower = input.to_lowercase();
    let mut matches: Vec<(&'static str, u32)> = ALL_TEMPLATES
        .iter()
        .map(|t| {
            let name = t.name();
            let sim = similarity_percentage(&input_lower, &name.to_lowercase());
            (name, sim)
        })
        .filter(|(_, sim)| *sim >= MIN_SIMILARITY_PERCENT)
        .collect();

    // Sort by similarity (highest first)
    matches.sort_by(|a, b| b.1.cmp(&a.1));

    // Return top 3 matches
    matches.truncate(3);
    matches
}
