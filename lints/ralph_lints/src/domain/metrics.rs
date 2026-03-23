//! Pure metrics calculation for boundary function analysis.
//!
//! This contains pure computation functions with no I/O effects.

/// Count boolean operators (&&, ||) in source.
pub fn count_boolean_operators(source: &str) -> usize {
    source.matches("&&").count() + source.matches("||").count()
}

#[cfg(test)]
/// Count decision points (if, match, for, while, loop) in source. Test-only.
pub fn count_decision_points(source: &str) -> usize {
    source
        .split(|ch: char| !ch.is_alphanumeric() && ch != '_')
        .filter(|token| matches!(*token, "if" | "match" | "for" | "while" | "loop"))
        .count()
}
