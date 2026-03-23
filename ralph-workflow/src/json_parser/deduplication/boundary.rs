//! Boundary module for inherently iterative deduplication algorithms.
//!
//! This module contains KMP and rolling hash implementations that are
//! fundamentally iterative and cannot be cleanly expressed in functional style.
//!
//! These functions are exempt from functional programming lints.

use std::collections::HashMap;

fn kmp_fail_dispatch(lps: &mut [usize], len: &mut usize, i: &mut usize, byte: u8, prev: u8) {
    if byte == prev {
        *len = len.saturating_add(1);
        lps[*i] = *len;
        *i = i.saturating_add(1);
    } else if *len != 0 {
        *len = lps[*len - 1];
    } else {
        lps[*i] = 0;
        *i = i.saturating_add(1);
    }
}

fn kmp_build_failure_table(pattern_bytes: &[u8], m: usize) -> Vec<usize> {
    let mut lps = vec![0; m];
    let mut len = 0usize;
    let mut i = 1usize;
    while i < m {
        let (bi, bl) = (pattern_bytes[i], pattern_bytes[len]);
        kmp_fail_dispatch(&mut lps, &mut len, &mut i, bi, bl);
    }
    lps
}

fn kmp_search_on_match(i: &mut usize, j: &mut usize, m: usize) -> Option<usize> {
    *i = i.saturating_add(1);
    *j = j.saturating_add(1);
    if *j == m {
        Some(*i - *j)
    } else {
        None
    }
}

fn kmp_search_miss(failure: &[usize], i: &mut usize, j: &mut usize) {
    if *j != 0 {
        *j = failure[*j - 1];
    } else {
        *i = i.saturating_add(1);
    }
}

fn kmp_search_step(
    text_bytes: &[u8],
    pattern_bytes: &[u8],
    failure: &[usize],
    i: &mut usize,
    j: &mut usize,
    m: usize,
) -> Option<usize> {
    if pattern_bytes[*j] == text_bytes[*i] {
        kmp_search_on_match(i, j, m)
    } else {
        kmp_search_miss(failure, i, j);
        None
    }
}

pub struct KMPMatcher {
    pattern: String,
    failure: Vec<usize>,
}

impl KMPMatcher {
    #[must_use]
    pub fn new(pattern: &str) -> Self {
        let pattern = pattern.to_string();
        let failure = Self::compute_failure(&pattern);
        Self { pattern, failure }
    }

    fn compute_failure(pattern: &str) -> Vec<usize> {
        let m = pattern.len();
        if m == 0 {
            return Vec::new();
        }
        let pattern_bytes = pattern.as_bytes();
        kmp_build_failure_table(pattern_bytes, m)
    }

    fn kmp_srch(&self, text: &[u8], n: usize, m: usize) -> Option<usize> {
        let (mut i, mut j, mut found) = (0, 0, None);
        while i < n && found.is_none() {
            found = kmp_search_step(
                text,
                self.pattern.as_bytes(),
                &self.failure,
                &mut i,
                &mut j,
                m,
            );
        }
        found
    }

    #[must_use]
    pub fn find(&self, text: &str) -> Option<usize> {
        let n = text.len();
        let m = self.pattern.len();
        if m == 0 || n < m {
            return None;
        }
        self.kmp_srch(text.as_bytes(), n, m)
    }

    #[cfg(test)]
    #[must_use]
    pub fn find_all(&self, text: &str) -> Vec<usize> {
        let mut positions = Vec::new();
        let n = text.len();
        let m = self.pattern.len();

        if m == 0 || n < m {
            return positions;
        }

        let text_bytes = text.as_bytes();
        let pattern_bytes = self.pattern.as_bytes();

        let mut i = 0;
        let mut j = 0;

        while i < n {
            if pattern_bytes[j] == text_bytes[i] {
                i = i.saturating_add(1);
                j = j.saturating_add(1);

                if j == m {
                    positions.push(i - j);
                    j = self.failure[j - 1];
                }
            } else if j != 0 {
                j = self.failure[j - 1];
            } else {
                i = i.saturating_add(1);
            }
        }

        positions
    }

    #[cfg(test)]
    #[must_use]
    pub const fn pattern_len(&self) -> usize {
        self.pattern.len()
    }

    #[cfg(test)]
    #[must_use]
    pub const fn is_empty(&self) -> bool {
        self.pattern.is_empty()
    }

    #[cfg(test)]
    #[must_use]
    pub(crate) fn failure_table(&self) -> &[usize] {
        &self.failure
    }
}

#[derive(Debug, Default, Clone)]
pub struct RollingHashWindow {
    content: String,
    cached_hashes: HashMap<usize, Vec<(usize, u64)>>,
}

impl RollingHashWindow {
    const BASE: u64 = 256;
    const MODULUS: u64 = 2_147_483_647;

    #[cfg(test)]
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    #[must_use]
    pub fn compute_hash(text: &str) -> u64 {
        text.bytes().fold(0u64, |hash, byte| {
            (hash * Self::BASE + u64::from(byte)) % Self::MODULUS
        })
    }

    #[cfg(test)]
    fn compute_power(power: usize) -> u64 {
        (0..power).fold(1u64, |result, _| (result * Self::BASE) % Self::MODULUS)
    }

    #[cfg(test)]
    pub fn add_content(&mut self, text: &str) {
        if text.is_empty() {
            return;
        }

        self.content.push_str(text);
        self.cached_hashes.clear();
    }

    #[cfg(test)]
    pub fn get_window_hashes(&mut self, window_size: usize) -> Vec<(usize, u64)> {
        if let Some(hashes) = self.cached_hashes.get(&window_size) {
            return hashes.clone();
        }

        let content_bytes = self.content.as_bytes();
        let content_len = content_bytes.len();

        if content_len < window_size {
            let empty: Vec<(usize, u64)> = Vec::new();
            self.cached_hashes.insert(window_size, empty.clone());
            return empty;
        }

        let mut hashes = Vec::new();
        let mut hash: u64 = 0;

        for byte in content_bytes.iter().take(window_size) {
            hash = (hash * Self::BASE + u64::from(*byte)) % Self::MODULUS;
        }
        hashes.push((0, hash));

        let power = Self::compute_power(window_size - 1);

        for i in 1..=(content_len - window_size) {
            let leftmost = u64::from(content_bytes[i - 1]);
            let removed = (leftmost * power) % Self::MODULUS;
            hash = (hash + Self::MODULUS - removed) % Self::MODULUS;

            hash = (hash * Self::BASE) % Self::MODULUS;
            let new_char = u64::from(content_bytes[i + window_size - 1]);
            hash = (hash + new_char) % Self::MODULUS;

            hashes.push((i, hash));
        }

        self.cached_hashes.insert(window_size, hashes.clone());
        hashes
    }

    #[cfg(test)]
    pub fn contains_hash(&mut self, hash: u64, window_size: usize) -> Option<usize> {
        let hashes = self.get_window_hashes(window_size);
        hashes
            .into_iter()
            .find(|(_, h)| *h == hash)
            .map(|(pos, _)| pos)
    }

    pub fn clear(&mut self) {
        self.content.clear();
        self.cached_hashes.clear();
    }

    #[cfg(test)]
    #[must_use]
    pub const fn len(&self) -> usize {
        self.content.len()
    }

    #[cfg(test)]
    #[must_use]
    pub const fn is_empty(&self) -> bool {
        self.content.is_empty()
    }
}
