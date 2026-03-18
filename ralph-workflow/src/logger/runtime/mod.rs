//! Runtime module for logger - contains OS-boundary code.
//!
//! This module satisfies the dylint boundary-module check for code that uses
//! interior mutability (LazyLock, Mutex, etc.) for caching or process communication,
//! and for code that accesses environment variables or terminal state.

pub mod ansi;
pub mod environment;

pub use ansi::ANSI_RE;
pub use environment::{ColorEnvironment, RealColorEnvironment};
