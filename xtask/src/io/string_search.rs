//! Re-exports of string-search algorithms from the domain layer.
//!
//! The KMP and Two-Way implementations have been moved to `crate::domain::string_search`
//! where they are implemented without imperative loops using functional combinators.
//! This module re-exports the public symbols for callers that import via
//! `crate::io::string_search`.

pub(crate) use crate::domain::string_search::critical_factorization;

#[cfg(test)]
pub(crate) use crate::domain::string_search::{kmp_search, tw_contains_precomputed};
