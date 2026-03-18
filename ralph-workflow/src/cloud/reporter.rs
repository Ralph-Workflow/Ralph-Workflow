//! Cloud reporter trait and implementations.
//!
//! This module re-exports from the io boundary module.

pub use crate::cloud::io::http_client::{HttpCloudReporter, HttpCloudReporter as CloudReporter};
pub use crate::cloud::NoopCloudReporter;
