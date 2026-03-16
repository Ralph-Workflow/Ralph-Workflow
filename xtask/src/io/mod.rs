// DO NOT CHANGE LINTING POLICY UNLESS THE USER SPECIFICALLY ASKS TO, YOU MUST REFACTOR EVEN IF IT TAKES YOU LONG TIME
#![deny(warnings)]
#![deny(clippy::all)]
#![forbid(unsafe_code)]

//! Boundary module for I/O operations in xtask.
//!
//! This module exists to host code that inherently requires mutation
//! (process handles, I/O, caching with interior mutability).
//! The forbid_mutating_receiver_methods lint permits &mut self methods
//! in boundary modules.

pub mod cache;
pub mod process;
pub mod scanner;
