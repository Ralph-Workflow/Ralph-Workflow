//! Runtime module for logger - contains OS-boundary code.
//!
//! This module satisfies the dylint boundary-module check for code that uses
//! interior mutability (LazyLock, Mutex, etc.) for caching or process communication.

pub mod ansi;

pub use ansi::ANSI_RE;
