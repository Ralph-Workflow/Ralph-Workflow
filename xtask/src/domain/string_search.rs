//! Pure string-search algorithms: KMP and Two-Way.
//!
//! Functional implementations using iterators, fold, and successors.
//! No imperative loops or mutable bindings.

// ── KMP ───────────────────────────────────────────────────────────────────────

/// Knuth-Morris-Pratt single-pattern search with O(n + m) worst-case performance.
pub(crate) fn kmp_search(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() {
        return Some(0);
    }
    let fail = build_kmp_table(needle);
    kmp_scan(haystack, needle, &fail)
}

fn build_kmp_table(needle: &[u8]) -> Vec<usize> {
    (1..needle.len()).fold(vec![0usize; needle.len()], |fail, i| {
        kmp_table_step(fail, needle, i)
    })
}

fn kmp_table_step(mut fail: Vec<usize>, needle: &[u8], i: usize) -> Vec<usize> {
    let k = kmp_follow_links(&fail, needle, fail[i - 1], needle[i]);
    fail[i] = if needle[k] == needle[i] { k + 1 } else { k };
    fail
}

fn kmp_scan(haystack: &[u8], needle: &[u8], fail: &[usize]) -> Option<usize> {
    let m = needle.len();
    haystack
        .iter()
        .enumerate()
        .try_fold(0usize, |q, (i, &c)| {
            let q = kmp_follow_links(fail, needle, q, c);
            let q = if needle[q] == c { q + 1 } else { q };
            if q == m {
                Err(i + 1 - m)
            } else {
                Ok(q)
            }
        })
        .err()
}

/// Follow KMP failure links until needle[k] matches target or k == 0.
fn kmp_follow_links(fail: &[usize], needle: &[u8], k: usize, target: u8) -> usize {
    std::iter::successors(Some(k), |&k| {
        if k > 0 && needle[k] != target {
            Some(fail[k - 1])
        } else {
            None
        }
    })
    .last()
    .unwrap_or(0)
}

// ── Two-Way ───────────────────────────────────────────────────────────────────

/// Two-Way search with a precomputed critical factorization (l, p).
pub(crate) fn tw_contains_precomputed(
    text: &[u8],
    pattern: &[u8],
    precomputed: (usize, usize),
) -> bool {
    let m = pattern.len();
    if m == 0 {
        return true;
    }
    let n = text.len();
    if m > n {
        return false;
    }
    let (l, p) = precomputed;
    let is_periodic = m > p && pattern[..m - p] == pattern[p..];
    if is_periodic {
        tw_periodic(text, pattern, l, p, n, m)
    } else {
        tw_nonperiodic(text, pattern, l, m, n)
    }
}

pub(crate) fn tw_periodic(
    text: &[u8],
    pattern: &[u8],
    l: usize,
    p: usize,
    n: usize,
    m: usize,
) -> bool {
    // State: (j, memory). Advance via successors until j+m > n.
    // Return true as soon as a match is confirmed.
    std::iter::successors(Some((0usize, usize::MAX)), |&(j, memory)| {
        if j + m > n {
            None
        } else {
            Some(tw_periodic_advance(text, pattern, l, p, m, j, memory))
        }
    })
    .any(|(j, memory)| j + m <= n && tw_periodic_confirmed_match(text, pattern, l, m, j, memory))
}

fn tw_periodic_advance(
    text: &[u8],
    pattern: &[u8],
    l: usize,
    p: usize,
    m: usize,
    j: usize,
    memory: usize,
) -> (usize, usize) {
    let start_i = l.max(memory.saturating_add(1).min(m));
    let right_mismatch = (start_i..m).find(|&i| pattern[i] != text[j + i]);
    match right_mismatch {
        Some(i) => (j + i.saturating_sub(l).max(1), usize::MAX),
        None => {
            let start_k = if memory == usize::MAX { 0 } else { memory + 1 };
            let left_mismatch = (start_k..=l).find(|&k| pattern[k] != text[j + k]);
            if left_mismatch.is_none() {
                // Match confirmed — advance by p to allow `any` to detect it.
                (j + p, m - p - 1)
            } else {
                (j + p, m - p - 1)
            }
        }
    }
}

fn tw_periodic_confirmed_match(
    text: &[u8],
    pattern: &[u8],
    l: usize,
    m: usize,
    j: usize,
    memory: usize,
) -> bool {
    if j + m > text.len() {
        return false;
    }
    let start_i = l.max(memory.saturating_add(1).min(m));
    let right_ok = (start_i..m).all(|i| pattern[i] == text[j + i]);
    if !right_ok {
        return false;
    }
    let start_k = if memory == usize::MAX { 0 } else { memory + 1 };
    (start_k..=l).all(|k| pattern[k] == text[j + k])
}

pub(crate) fn tw_nonperiodic(text: &[u8], pattern: &[u8], l: usize, m: usize, n: usize) -> bool {
    let slide = l.max(m.saturating_sub(l + 1)) + 1;
    std::iter::successors(Some(0usize), |&j| {
        if j + m > n {
            None
        } else {
            Some(tw_nonperiodic_advance(text, pattern, l, m, slide, j))
        }
    })
    .any(|j| j + m <= n && tw_nonperiodic_is_match(text, pattern, l, m, j))
}

fn tw_nonperiodic_advance(
    text: &[u8],
    pattern: &[u8],
    l: usize,
    m: usize,
    slide: usize,
    j: usize,
) -> usize {
    let mismatch = (l..m).find(|&i| pattern[i] != text[j + i]);
    match mismatch {
        Some(i) => j + (i - l) + 1,
        None => j + slide,
    }
}

fn tw_nonperiodic_is_match(text: &[u8], pattern: &[u8], l: usize, m: usize, j: usize) -> bool {
    (l..m).all(|i| pattern[i] == text[j + i]) && (0..=l).rev().all(|k| pattern[k] == text[j + k])
}

// ── Critical factorization ────────────────────────────────────────────────────

/// Compute the critical factorization (l, p) for the given pattern.
pub(crate) fn critical_factorization(pattern: &[u8]) -> (usize, usize) {
    let (l1, p1) = max_suffix(pattern, false);
    let (l2, p2) = max_suffix(pattern, true);
    if l1 >= l2 {
        (l1, p1)
    } else {
        (l2, p2)
    }
}

pub(crate) fn max_suffix(pattern: &[u8], rev: bool) -> (usize, usize) {
    let m = pattern.len();
    // State: (ms, j, k, p). Advance until j+k >= m.
    let (ms, _j, _k, p) = std::iter::successors(
        Some((usize::MAX, 0usize, 1usize, 1usize)),
        |&(ms, j, k, p)| {
            if j + k >= m {
                None
            } else {
                Some(max_suffix_step(pattern, rev, ms, j, k, p))
            }
        },
    )
    .last()
    .unwrap_or((usize::MAX, 0, 1, 1));
    (if ms == usize::MAX { 0 } else { ms + 1 }, p)
}

fn max_suffix_step(
    pattern: &[u8],
    rev: bool,
    ms: usize,
    j: usize,
    k: usize,
    p: usize,
) -> (usize, usize, usize, usize) {
    let cmp_j = pattern[j + k];
    let cmp_ms = if ms == usize::MAX {
        pattern[k - 1]
    } else {
        pattern[ms + k]
    };
    let gt = if rev { cmp_j < cmp_ms } else { cmp_j > cmp_ms };
    let lt = if rev { cmp_j > cmp_ms } else { cmp_j < cmp_ms };
    if gt {
        (
            ms,
            j + k,
            1,
            // When ms is unset (MAX), wrapping_sub(MAX) == j+k+1, matching original Two-Way behavior.
            (j + k).wrapping_sub(if ms == usize::MAX { usize::MAX } else { ms }),
        )
    } else if lt {
        let new_ms = if ms == usize::MAX { j } else { ms.max(j) };
        (new_ms, new_ms.wrapping_add(1), 1, 1)
    } else if k == p {
        (ms, j + p, 1, p)
    } else {
        (ms, j, k + 1, p)
    }
}
