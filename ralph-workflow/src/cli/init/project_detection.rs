// Project type detection and fuzzy matching logic.
//
// This file is included via include!() macro from the parent init.rs module.
// Contains Levenshtein distance calculation and template name fuzzy matching.

fn levenshtein_distance(a: &str, b: &str) -> usize {
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

    let final_row = b_chars.iter().enumerate().fold(
        (0..=a_len).collect::<Vec<usize>>(),
        |prev_row, (j, b_char)| {
            let first_val = j;
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
    ALL_TEMPLATES
        .iter()
        .map(|t| {
            let name = t.name();
            let sim = similarity_percentage(&input_lower, &name.to_lowercase());
            (name, sim)
        })
        .filter(|(_, sim)| *sim >= MIN_SIMILARITY_PERCENT)
        .sorted_by(|a, b| b.1.cmp(&a.1))
        .take(3)
        .collect()
}
