//! Boundary module for memory metrics test utilities.
//! This module is exempt from functional Rust lints due to interior mutability requirements.

use std::cell::RefCell;

pub struct SnapshotCounter {
    count: RefCell<usize>,
}

impl SnapshotCounter {
    pub fn new() -> Self {
        Self {
            count: RefCell::new(0),
        }
    }

    pub fn get(&self) -> usize {
        *self.count.borrow()
    }

    pub fn increment(&self) {
        *self.count.borrow_mut() += 1;
    }
}

impl Default for SnapshotCounter {
    fn default() -> Self {
        Self::new()
    }
}
