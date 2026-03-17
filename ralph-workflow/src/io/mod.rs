//! I/O boundary module.
//!
//! Handles filesystem operations and external data transport.
//! All code in this module may use mutation, imperative loops, and I/O.
//!
//! Domain logic and business rules must NOT live here.
//!
//! ## Module Organization
//!
//! This module provides thin wrappers around filesystem operations.
//! Pure business logic should live in domain modules.
//!
//! ## Key Principles
//!
//! - Use [`crate::workspace::Workspace`] trait for all file operations
//! - Keep this module focused on transport, not business decisions
//! - Return raw data, let domain code interpret it

pub mod file_operations;
pub mod workspace_ops;
