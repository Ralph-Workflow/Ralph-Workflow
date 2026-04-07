//! Tests for fault-tolerant agent execution
//!
//! This module tests the fault tolerance mechanisms in agent execution:
//! - Basic execution with timeout and usage limit handling
//! - Error classification for agent errors (rate limit, auth, network, crashes)
//! - Error classification for I/O errors (timeout, filesystem, network)
//! - Error type predicates (`is_timeout`, `is_rate_limit`, `is_auth`, `is_retriable`)
//! - Fallback behavior triggered by different error types
//!
//! ## Test Organization
//!
//! - `basic_execution` - Core execution flow and basic error classification
//! - `rate_limit_patterns` - Comprehensive rate limit detection across providers
//! - `error_predicates` - Error type predicates and fallback tests

mod basic_execution;
mod error_predicates;
mod proprietary_exit_codes;
mod rate_limit_patterns;
mod timeout_output_kind;

use super::*;
use crate::agents::JsonParserType;
use crate::config::Config;
use crate::logger::{Colors, Logger};
use crate::pipeline::{PipelineRuntime, Timer};
use crate::reducer::event::AgentEvent;
use crate::workspace::{MemoryWorkspace, Workspace};
use std::collections::HashMap;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Test helper: Workspace that times out when writing to a specific path
///
/// Used to test timeout handling when saving prompts or other files.
#[derive(Debug, Clone)]
pub(super) struct TimedOutWriteWorkspace {
    inner: MemoryWorkspace,
    fail_path: PathBuf,
}

impl TimedOutWriteWorkspace {
    pub(super) fn new(inner: MemoryWorkspace, fail_path: PathBuf) -> Self {
        Self { inner, fail_path }
    }
}

impl Workspace for TimedOutWriteWorkspace {
    fn root(&self) -> &Path {
        self.inner.root()
    }

    fn read(&self, relative: &Path) -> io::Result<String> {
        self.inner.read(relative)
    }

    fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
        self.inner.read_bytes(relative)
    }

    fn write(&self, relative: &Path, content: &str) -> io::Result<()> {
        if relative == self.fail_path.as_path() {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "simulated write timeout",
            ));
        }
        self.inner.write(relative, content)
    }

    fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.inner.write_bytes(relative, content)
    }

    fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.inner.append_bytes(relative, content)
    }

    fn exists(&self, relative: &Path) -> bool {
        self.inner.exists(relative)
    }

    fn is_file(&self, relative: &Path) -> bool {
        self.inner.is_file(relative)
    }

    fn is_dir(&self, relative: &Path) -> bool {
        self.inner.is_dir(relative)
    }

    fn remove(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove(relative)
    }

    fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_if_exists(relative)
    }

    fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_dir_all(relative)
    }

    fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_dir_all_if_exists(relative)
    }

    fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
        self.inner.create_dir_all(relative)
    }

    fn read_dir(&self, relative: &Path) -> io::Result<Vec<crate::workspace::DirEntry>> {
        self.inner.read_dir(relative)
    }

    fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
        self.inner.rename(from, to)
    }

    fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
        self.inner.write_atomic(relative, content)
    }

    fn set_readonly(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_readonly(relative)
    }

    fn set_writable(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_writable(relative)
    }
}

/// Test helper: Workspace that fails to read a specific path.
///
/// Used to simulate missing logfiles for timeout output detection tests.
#[derive(Debug, Clone)]
pub(super) struct ReadFailWorkspace {
    inner: MemoryWorkspace,
    fail_path: PathBuf,
}

impl ReadFailWorkspace {
    pub(super) fn new(inner: MemoryWorkspace, fail_path: PathBuf) -> Self {
        Self { inner, fail_path }
    }
}

impl Workspace for ReadFailWorkspace {
    fn root(&self) -> &Path {
        self.inner.root()
    }

    fn read(&self, relative: &Path) -> io::Result<String> {
        if relative == self.fail_path.as_path() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "simulated missing file",
            ));
        }
        self.inner.read(relative)
    }

    fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
        if relative == self.fail_path.as_path() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "simulated missing file",
            ));
        }
        self.inner.read_bytes(relative)
    }

    fn write(&self, relative: &Path, content: &str) -> io::Result<()> {
        self.inner.write(relative, content)
    }

    fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.inner.write_bytes(relative, content)
    }

    fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.inner.append_bytes(relative, content)
    }

    fn exists(&self, relative: &Path) -> bool {
        self.inner.exists(relative)
    }

    fn is_file(&self, relative: &Path) -> bool {
        self.inner.is_file(relative)
    }

    fn is_dir(&self, relative: &Path) -> bool {
        self.inner.is_dir(relative)
    }

    fn remove(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove(relative)
    }

    fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_if_exists(relative)
    }

    fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_dir_all(relative)
    }

    fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_dir_all_if_exists(relative)
    }

    fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
        self.inner.create_dir_all(relative)
    }

    fn read_dir(&self, relative: &Path) -> io::Result<Vec<crate::workspace::DirEntry>> {
        self.inner.read_dir(relative)
    }

    fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
        self.inner.rename(from, to)
    }

    fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
        self.inner.write_atomic(relative, content)
    }

    fn set_readonly(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_readonly(relative)
    }

    fn set_writable(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_writable(relative)
    }
}

/// Test helper: Workspace that returns custom content for a specific read path.
///
/// Used to simulate agent logfiles containing structured error events without
/// modifying the mock agent output generator.
#[derive(Debug)]
pub(super) struct ReadHijackWorkspace {
    inner: MemoryWorkspace,
    hijack_path: PathBuf,
    hijack_content: String,
}

impl ReadHijackWorkspace {
    pub(super) fn new(
        inner: MemoryWorkspace,
        hijack_path: PathBuf,
        hijack_content: String,
    ) -> Self {
        Self {
            inner,
            hijack_path,
            hijack_content,
        }
    }
}

impl Workspace for ReadHijackWorkspace {
    fn root(&self) -> &Path {
        self.inner.root()
    }

    fn read(&self, relative: &Path) -> io::Result<String> {
        if relative == self.hijack_path.as_path() {
            return Ok(self.hijack_content.clone());
        }
        self.inner.read(relative)
    }

    fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
        self.inner.read_bytes(relative)
    }

    fn write(&self, relative: &Path, content: &str) -> io::Result<()> {
        self.inner.write(relative, content)
    }

    fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.inner.write_bytes(relative, content)
    }

    fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.inner.append_bytes(relative, content)
    }

    fn exists(&self, relative: &Path) -> bool {
        self.inner.exists(relative)
    }

    fn is_file(&self, relative: &Path) -> bool {
        self.inner.is_file(relative)
    }

    fn is_dir(&self, relative: &Path) -> bool {
        self.inner.is_dir(relative)
    }

    fn remove(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove(relative)
    }

    fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_if_exists(relative)
    }

    fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_dir_all(relative)
    }

    fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_dir_all_if_exists(relative)
    }

    fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
        self.inner.create_dir_all(relative)
    }

    fn read_dir(&self, relative: &Path) -> io::Result<Vec<crate::workspace::DirEntry>> {
        self.inner.read_dir(relative)
    }

    fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
        self.inner.rename(from, to)
    }

    fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
        self.inner.write_atomic(relative, content)
    }

    fn set_readonly(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_readonly(relative)
    }

    fn set_writable(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_writable(relative)
    }
}
