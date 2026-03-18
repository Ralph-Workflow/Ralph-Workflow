//! I/O boundary for cloud-mode HTTP transport and secret redaction.
//!
//! This module contains effectful edge code that must not leak into domain logic.

pub mod http;
pub mod redaction;

pub use http::HttpCloudReporter;
pub use redaction::{redact_bearer_tokens, redact_common_query_params, redact_token_like_substrings};
