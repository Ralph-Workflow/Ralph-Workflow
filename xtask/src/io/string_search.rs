/// Knuth-Morris-Pratt single-pattern search with O(n + m) worst-case performance.
pub(crate) fn kmp_search(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() {
        return Some(0);
    }
    let m = needle.len();

    let mut fail = vec![0; m];
    let mut k = 0;
    for i in 1..m {
        while k > 0 && needle[k] != needle[i] {
            k = fail[k - 1];
        }
        if needle[k] == needle[i] {
            k += 1;
        }
        fail[i] = k;
    }

    let mut q = 0;
    for (i, &c) in haystack.iter().enumerate() {
        while q > 0 && needle[q] != c {
            q = fail[q - 1];
        }
        if needle[q] == c {
            q += 1;
        }
        if q == m {
            return Some(i + 1 - m);
        }
    }
    None
}

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
        let mut j = 0;
        let mut memory = usize::MAX;
        while j + m <= n {
            let mut i = l
                .max(if memory == usize::MAX { 0 } else { memory })
                .saturating_add(1);
            while i < m && pattern[i] == text[j + i] {
                i += 1;
            }
            if i < m {
                j += i.saturating_sub(l).max(1);
                memory = usize::MAX;
            } else {
                let start_k = if memory == usize::MAX { 0 } else { memory + 1 };
                let mut k = start_k;
                while k <= l && pattern[k] == text[j + k] {
                    k += 1;
                }
                if k > l {
                    return true;
                }
                j += p;
                memory = m - p - 1;
            }
        }
    } else {
        let slide = l.max(m.saturating_sub(l + 1)) + 1;
        let mut j = 0;
        while j + m <= n {
            let mut i = l;
            while i < m && pattern[i] == text[j + i] {
                i += 1;
            }
            if i < m {
                j += (i - l) + 1;
            } else {
                let full_match = (0..=l).rev().all(|k| pattern[k] == text[j + k]);
                if full_match {
                    return true;
                }
                j += slide;
            }
        }
    }
    false
}

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

fn max_suffix(pattern: &[u8], rev: bool) -> (usize, usize) {
    let m = pattern.len();
    let mut ms = usize::MAX;
    let mut j = 0;
    let mut k = 1;
    let mut p = 1;
    while j + k < m {
        let cmp_j = pattern[j + k];
        let cmp_ms = if ms == usize::MAX {
            pattern[k - 1]
        } else {
            pattern[ms + k]
        };
        let gt = if rev { cmp_j < cmp_ms } else { cmp_j > cmp_ms };
        let lt = if rev { cmp_j > cmp_ms } else { cmp_j < cmp_ms };
        if gt {
            j += k;
            k = 1;
            p = j.wrapping_sub(if ms == usize::MAX { usize::MAX } else { ms });
        } else if lt {
            ms = if ms == usize::MAX { j } else { ms.max(j) };
            j = ms.wrapping_add(1);
            k = 1;
            p = 1;
        } else if k == p {
            j += p;
            k = 1;
        } else {
            k += 1;
        }
    }
    let final_ms = if ms == usize::MAX { 0 } else { ms + 1 };
    (final_ms, p)
}
