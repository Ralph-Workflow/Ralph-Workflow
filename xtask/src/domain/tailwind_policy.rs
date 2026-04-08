//! Pure domain policy for the tailwind4-removed-angular-classes check.
//!
//! This module contains only pure, stateless domain concepts:
//! - Tailwind class definitions
//! - Policy result determination
//! - Message formatting for violations
//!
//! All actual scanning logic lives in
//! `boundary/check_tailwind4_removed_angular_classes.rs`.

/// A Tailwind 3 class that was removed in Tailwind 4.
#[derive(Debug, Clone, Copy)]
pub struct RemovedTailwindClass {
    pub literal: &'static str,
    pub replacement: &'static str,
    pub is_prefix: bool,
}

/// All Tailwind 3-only classes checked by this rule.
pub const REMOVED_TAILWIND4_ANGULAR_CLASSES: &[RemovedTailwindClass] = &[
    RemovedTailwindClass {
        literal: "bg-opacity-",
        replacement: "bg-<color>/<opacity>",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "text-opacity-",
        replacement: "text-<color>/<opacity>",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "border-opacity-",
        replacement: "border-<color>/<opacity>",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "divide-opacity-",
        replacement: "divide-<color>/<opacity>",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "ring-opacity-",
        replacement: "ring-<color>/<opacity>",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "placeholder-opacity-",
        replacement: "placeholder-<color>/<opacity>",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "flex-shrink-",
        replacement: "shrink-*",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "flex-grow-",
        replacement: "grow-*",
        is_prefix: true,
    },
    RemovedTailwindClass {
        literal: "overflow-ellipsis",
        replacement: "text-ellipsis",
        is_prefix: false,
    },
    RemovedTailwindClass {
        literal: "decoration-slice",
        replacement: "box-decoration-slice",
        is_prefix: false,
    },
    RemovedTailwindClass {
        literal: "decoration-clone",
        replacement: "box-decoration-clone",
        is_prefix: false,
    },
];

/// Pure domain: determine if a tailwind candidate matches a removed class rule.
pub fn tailwind_candidate_matches_rule(candidate: &str, rule: &RemovedTailwindClass) -> bool {
    if rule.is_prefix {
        candidate.starts_with(rule.literal)
    } else {
        candidate == rule.literal
    }
}

/// Pure domain: normalize a tailwind class candidate by stripping modifiers.
///
/// Strips leading `!` (important prefix) and extracts the last class segment
/// after splitting on `:` (e.g., `sm:md:flex-shrink-0` -> `flex-shrink-0`).
pub fn normalize_tailwind_candidate(token: &str) -> &str {
    token
        .rsplit(':')
        .next()
        .unwrap_or(token)
        .trim_start_matches('!')
        .trim_end_matches('!')
}

/// Pure domain: check if a byte is a valid tailwind token character.
pub fn is_tailwind_token_char(byte: u8) -> bool {
    byte.is_ascii_alphanumeric()
        || matches!(
            byte,
            b'-' | b'_' | b':' | b'/' | b'[' | b']' | b'!' | b'.' | b'(' | b')'
        )
}

/// Pure domain: extract a tailwind class token from a line at the given offset.
///
/// Returns the token string if a valid tailwind class is found at the offset.
pub fn extract_tailwind_token(line: &[u8], match_offset: usize) -> Option<String> {
    if match_offset >= line.len() {
        return None;
    }

    let start = find_token_start(line, match_offset);
    let end = find_token_end(line, match_offset);

    if start == end {
        return None;
    }

    Some(String::from_utf8_lossy(&line[start..end]).to_string())
}

fn find_token_start(line: &[u8], index: usize) -> usize {
    if index == 0 || !is_tailwind_token_char(line[index - 1]) {
        index
    } else {
        find_token_start(line, index - 1)
    }
}

fn find_token_end(line: &[u8], index: usize) -> usize {
    if index >= line.len() || !is_tailwind_token_char(line[index]) {
        index
    } else {
        find_token_end(line, index + 1)
    }
}
