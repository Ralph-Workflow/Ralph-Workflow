use crate::io::string_search::critical_factorization;

/// Boyer-Moore-Horspool single-pattern search.
///
/// Retained for test cross-validation against `tw_contains` (the production
/// algorithm).  Production code now uses `tw_contains` for O(n) worst-case
/// guarantees; BMH degenerates to O(n×m) on adversarial inputs.
///
/// Preprocessing: O(|alphabet| + |pattern|) — builds a 256-entry bad-character shift table.
/// Search: O(|text| / |pattern|) average, O(|text| × |pattern|) worst-case.
///
/// Reference: Horspool, R.N. (1980). "Practical Fast Searching in Strings."
/// Software: Practice and Experience 10(6): 501–506.
/// See also: TAOCP Vol. 3, §6.3 (String Searching).
#[cfg(test)]
pub(crate) fn bmh_contains(text: &[u8], pattern: &[u8]) -> bool {
    let m = pattern.len();
    if m == 0 {
        return true;
    }
    let n = text.len();
    if m > n {
        return false;
    }
    // Build bad-character shift table.
    let mut shift = [m; 256];
    for i in 0..m - 1 {
        shift[pattern[i] as usize] = m - 1 - i;
    }
    let mut i = m - 1;
    while i < n {
        let mut j = m - 1;
        let mut k = i;
        while j > 0 && text[k] == pattern[j] {
            k -= 1;
            j -= 1;
        }
        if j == 0 && text[k] == pattern[0] {
            return true;
        }
        i += shift[text[i] as usize];
    }

    false
}

/// Two-Way single-pattern byte search (Crochemore and Lecroq, 1991).
///
/// Implements the canonical two-case formulation from Crochemore & Lecroq,
/// "Handbook of Exact String-Matching Algorithms" (2004), Chapter 26.
///
/// ## Preprocessing — O(m)
/// Computes the critical factorization (l, p) via the lex-maximal suffix
/// algorithm.  No auxiliary arrays beyond two `usize` values.
///
/// ## Search — O(n) worst-case
/// **Case 1 (pattern is p-periodic globally):** uses memory optimisation;
/// O(n) total comparisons.
/// **Case 2 (pattern is NOT p-periodic globally):** slides by max(l,m-l-1)+1
/// on full-mismatch; still O(n) total.
///
/// Reference: Crochemore, M. and Lecroq, P. (1991). "Tight bounds on the
/// complexity of the Two-Way string-matching algorithm." Information Processing
/// Letters, 46(1):1–8. Also TAOCP Vol. 3, §6.3.
///
/// Preferred over BMH for NegativeLookahead because BMH degenerates to O(n×m)
/// on adversarial inputs (e.g. 1000×'a' + 'b' with pattern "aaab"), while
/// Two-Way guarantees O(n) in all cases.
#[cfg(test)]
pub(crate) fn tw_contains(text: &[u8], pattern: &[u8]) -> bool {
    let m = pattern.len();
    if m == 0 {
        return true;
    }
    let n = text.len();
    if m > n {
        return false;
    }

    let (l, p) = critical_factorization(pattern);

    let is_periodic = m > p && pattern[..m - p] == pattern[p..];

    if is_periodic {
        let mut j = 0usize;
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
        let mut j = 0usize;
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
