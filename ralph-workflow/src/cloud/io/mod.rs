//! I/O boundary for cloud network operations.
//!
//! This module contains the imperative network I/O code that cannot be
//! expressed functionally. The HTTP client implementation uses ureq
//! for making HTTP requests to the cloud API.

pub mod http_client;
