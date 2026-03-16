//! Levenshtein distance calculation for typo detection.
//!
//! This module provides string similarity matching to suggest corrections
//! for unknown configuration keys.

#[expect(
    clippy::expect_used,
    reason = "bounds verified by loop invariant: i < a.len(), j < b.len()"
)]
pub fn levenshtein_distance(a: &str, b: &str) -> usize {
    let a_len = a.len();
    let b_len = b.len();

    if a_len == 0 {
        return b_len;
    }
    if b_len == 0 {
        return a_len;
    }

    let mut prev_row: Vec<usize> = (0..=b_len).collect();
    let mut curr_row = vec![0; b_len + 1];

    for (i, a_char) in a.chars().enumerate() {
        // Safe: curr_row has length b_len + 1, and i + 1 <= b_len + 1
        *curr_row
            .first_mut()
            .expect("curr_row has at least 1 element") = i + 1;

        for (j, b_char) in b.chars().enumerate() {
            let cost = usize::from(a_char != b_char);
            // Safe: j + 1 <= b_len since j < b_len
            let new_val = std::cmp::min(
                std::cmp::min(
                    // Safe: j <= b_len - 1, curr_row[j] is valid
                    *curr_row.get(j).expect("j in range") + 1,
                    // Safe: j + 1 <= b_len, prev_row[j + 1] is valid
                    *prev_row.get(j + 1).expect("j+1 in range") + 1,
                ),
                // Safe: j <= b_len - 1, prev_row[j] is valid
                *prev_row.get(j).expect("j in range") + cost,
            );
            *curr_row.get_mut(j + 1).expect("j+1 in range") = new_val;
        }

        std::mem::swap(&mut prev_row, &mut curr_row);
    }

    // Safe: prev_row has length b_len + 1, b_len is valid index
    *prev_row.get(b_len).expect("b_len in range")
}

/// Find the closest valid key name for typo detection.
///
/// Returns the closest matching key name if one exists within the edit distance threshold.
///
/// # Arguments
///
/// * `unknown_key` - The potentially misspelled key
/// * `valid_keys` - List of valid key names
///
/// # Returns
///
/// `Some(String)` with the suggested key if a match is found within threshold,
/// `None` otherwise.
#[must_use]
pub fn suggest_key(unknown_key: &str, valid_keys: &[&str]) -> Option<String> {
    let threshold = 3; // Maximum edit distance for suggestions

    valid_keys
        .iter()
        .map(|&key| (key, levenshtein_distance(unknown_key, key)))
        .filter(|(_, distance)| *distance <= threshold)
        .min_by_key(|(_, distance)| *distance)
        .map(|(key, _)| key.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_levenshtein_distance() {
        assert_eq!(levenshtein_distance("", ""), 0);
        assert_eq!(levenshtein_distance("abc", "abc"), 0);
        assert_eq!(levenshtein_distance("abc", "abd"), 1);
        assert_eq!(levenshtein_distance("developer_iters", "develper_iters"), 1);
    }

    #[test]
    fn test_suggest_key() {
        let valid_keys = &["developer_iters", "reviewer_reviews", "verbosity"];

        assert_eq!(
            suggest_key("develper_iters", valid_keys),
            Some("developer_iters".to_string())
        );

        assert_eq!(
            suggest_key("verbozity", valid_keys),
            Some("verbosity".to_string())
        );

        // No suggestion for completely different key
        assert_eq!(suggest_key("completely_different", valid_keys), None);
    }
}
