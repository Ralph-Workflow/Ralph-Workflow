pub mod health_monitor;
pub mod runtime;
pub mod streaming_state;

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};

pub fn compute_hash(parts: &[&[u8]]) -> u64 {
    let mut hasher = DefaultHasher::new();
    for part in parts {
        hasher.write(part);
    }
    hasher.finish()
}

pub fn compute_hash_str(text: &str) -> u64 {
    let mut hasher = DefaultHasher::new();
    text.hash(&mut hasher);
    hasher.finish()
}
