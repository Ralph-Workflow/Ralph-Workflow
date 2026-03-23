//! Levenshtein distance calculation for typo detection.
//!
//! This module provides string similarity matching to suggest corrections
//! for unknown configuration keys.

pub fn levenshtein_distance(a: &str, b: &str) -> usize {
    let a_chars: Vec<char> = a.chars().collect();
    let b_chars: Vec<char> = b.chars().collect();
    let a_len = a_chars.len();
    let b_len = b_chars.len();

    if a_len == 0 {
        return b_len;
    }
    if b_len == 0 {
        return a_len;
    }

    // Standard algorithm: iterate over b as outer, a as inner
    // Functional style: use fold to build rows, scan for inner dependency
    let final_row = b_chars.iter().enumerate().fold(
        (0..=a_len).collect::<Vec<usize>>(),
        |prev_row, (j, b_char)| {
            // Use fold to compute current row - maintains the sequential dependency
            // curr_row[i+1] depends on curr_row[i]
            let first_val = j + 1;
            let curr_row: Vec<usize> = (0..=a_len)
                .scan(first_val, |prev_val, i| {
                    if i == 0 {
                        Some(first_val)
                    } else {
                        let cost = usize::from(*b_char != a_chars[i - 1]);
                        let curr = (*prev_val)
                            .saturating_add(1)
                            .min(prev_row[i].saturating_add(1))
                            .min(prev_row[i - 1].saturating_add(cost));
                        *prev_val = curr;
                        Some(curr)
                    }
                })
                .collect();
            curr_row
        },
    );

    *final_row.get(a_len).unwrap_or(&a_len)
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
