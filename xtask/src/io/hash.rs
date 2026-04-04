//! Hash computation for content-addressed caching.
//!
//! Provides functions for computing stable hashes of check scopes,
//! native scan definitions, and command definitions for cache invalidation.

use std::path::Path;

use crate::io::fingerprint::{
    compute_scope_hash_with_snapshot, Fnv1aHasher, RepositoryFingerprintCache,
};
use crate::io::scanner::{MatchMode, NativeScanCheck};
use crate::io::scope::{
    native_required_scope_for, CheckScope, COMMAND_DEFINITION_HASH_VERSION,
    NATIVE_REQUIRED_HASH_VERSION, NATIVE_SCAN_HASH_VERSION,
};
use crate::runtime::verify::CommandSpec;

/// Append the hash of a native scan definition to the hasher.
pub fn append_native_scan_definition_hash(hasher: &mut Fnv1aHasher, checks: &[NativeScanCheck]) {
    for check in checks {
        append_native_scan_check_definition(hasher, check);
    }
}

fn append_native_scan_check_definition(hasher: &mut Fnv1aHasher, check: &NativeScanCheck) {
    append_str(hasher, check.name);
    append_strs(hasher, check.literals);
    append_strs(hasher, check.directories);
    append_str(hasher, check.include_glob);
    append_strs(hasher, check.exclude_globs);
    append_match_mode(hasher, check.mode);
}

fn append_strs(hasher: &mut Fnv1aHasher, values: &[&str]) {
    for value in values {
        append_str(hasher, value);
    }
}

fn append_str(hasher: &mut Fnv1aHasher, value: &str) {
    hasher.write_bytes(value.as_bytes());
}

fn append_match_mode(hasher: &mut Fnv1aHasher, mode: MatchMode) {
    match mode {
        MatchMode::AnyLiteral { skip_comment_lines } => {
            hasher.write_bytes(b"any-literal");
            hasher.write_bytes(&[u8::from(skip_comment_lines)]);
        }
        MatchMode::StemWithBoolSuffix => {
            hasher.write_bytes(b"stem-with-bool-suffix");
        }
        MatchMode::AnyLiteralAtLineStart { skip_comment_lines } => {
            hasher.write_bytes(b"any-literal-at-line-start");
            hasher.write_bytes(&[u8::from(skip_comment_lines)]);
        }
        MatchMode::NegativeLookahead {
            negative_context,
            word_boundary_at_end,
        } => {
            hasher.write_bytes(b"negative-lookahead");
            hasher.write_bytes(negative_context.as_bytes());
            hasher.write_bytes(&[u8::from(word_boundary_at_end)]);
        }
    }
}

pub fn compute_native_scan_hash(
    repo_root: &Path,
    checks: &[crate::io::scanner::NativeScanCheck],
    snapshot: &RepositoryFingerprintCache,
) -> std::io::Result<u64> {
    let mut hasher = Fnv1aHasher::new();
    hasher.write_bytes(NATIVE_SCAN_HASH_VERSION);
    append_native_scan_definition_hash(&mut hasher, checks);

    let all_paths = collect_native_scan_paths(repo_root, checks, snapshot)?;

    for path in &all_paths {
        let relative = path.strip_prefix(repo_root).unwrap_or(path);
        hasher.write_bytes(relative.to_string_lossy().as_bytes());
        hasher.write_bytes(&snapshot.read_file_digest(path)?.to_le_bytes());
    }

    Ok(hasher.finish())
}

fn collect_native_scan_paths(
    repo_root: &Path,
    checks: &[NativeScanCheck],
    snapshot: &RepositoryFingerprintCache,
) -> std::io::Result<Vec<std::path::PathBuf>> {
    let mut all_paths = collect_paths_from_checks(repo_root, checks, snapshot)?;
    add_cargo_and_toolchain_files(repo_root, &mut all_paths);
    extend_xtask_rs_files(repo_root, snapshot, &mut all_paths)?;
    finalize_paths(&mut all_paths);
    Ok(all_paths)
}

fn collect_paths_from_checks(
    repo_root: &Path,
    checks: &[NativeScanCheck],
    snapshot: &RepositoryFingerprintCache,
) -> std::io::Result<Vec<std::path::PathBuf>> {
    let mut paths = Vec::new();
    for check in checks {
        for dir in check.directories {
            paths.extend(snapshot.collect_globbed_paths_excluding(
                repo_root,
                dir,
                check.include_glob,
                check.exclude_globs,
            )?);
        }
    }
    Ok(paths)
}

fn add_cargo_and_toolchain_files(repo_root: &Path, paths: &mut Vec<std::path::PathBuf>) {
    for rel in [
        "Cargo.toml",
        "Cargo.lock",
        "xtask/Cargo.toml",
        "rust-toolchain.toml",
        "rust-toolchain",
    ] {
        let path = repo_root.join(rel);
        if path.exists() {
            paths.push(path);
        }
    }
}

fn extend_xtask_rs_files(
    repo_root: &Path,
    snapshot: &RepositoryFingerprintCache,
    paths: &mut Vec<std::path::PathBuf>,
) -> std::io::Result<()> {
    paths.extend(snapshot.collect_globbed_paths(repo_root, "xtask/src", "*.rs")?);
    Ok(())
}

fn finalize_paths(paths: &mut Vec<std::path::PathBuf>) {
    paths.sort();
    paths.dedup();
}

pub fn compute_native_required_hash(
    repo_root: &Path,
    check: &crate::runtime::verify::NativeCheck,
    snapshot: &RepositoryFingerprintCache,
) -> std::io::Result<u64> {
    let mut hasher = Fnv1aHasher::new();
    hasher.write_bytes(NATIVE_REQUIRED_HASH_VERSION);
    hasher.write_bytes(check.name.as_bytes());

    let input_hash = compute_scope_hash_with_snapshot(
        repo_root,
        &native_required_scope_for(check.name),
        snapshot,
    )?;
    hasher.write_bytes(&input_hash.to_le_bytes());

    let implementation_hash =
        compute_scope_hash_with_snapshot(repo_root, &CheckScope::Build(&["xtask/src"]), snapshot)?;
    hasher.write_bytes(&implementation_hash.to_le_bytes());

    Ok(hasher.finish())
}

pub fn compute_xtask_implementation_hash(
    repo_root: &Path,
    snapshot: &RepositoryFingerprintCache,
) -> std::io::Result<u64> {
    compute_scope_hash_with_snapshot(repo_root, &CheckScope::Build(&["xtask/src"]), snapshot)
}

pub fn compute_command_definition_hash(
    repo_root: &Path,
    spec: &CommandSpec,
    snapshot: &RepositoryFingerprintCache,
) -> std::io::Result<u64> {
    let mut hasher = Fnv1aHasher::new();
    hasher.write_bytes(COMMAND_DEFINITION_HASH_VERSION);
    append_command_spec_fields(&mut hasher, spec);
    hasher.write_bytes(&compute_xtask_implementation_hash(repo_root, snapshot)?.to_le_bytes());
    Ok(hasher.finish())
}

fn append_command_spec_fields(hasher: &mut Fnv1aHasher, spec: &CommandSpec) {
    append_str(hasher, spec.name);
    append_str(hasher, spec.program);
    append_strs(hasher, spec.args);
    append_exit_codes(hasher, spec.success_exit_codes);
    append_env_pairs(hasher, spec.extra_env);
}

fn append_exit_codes(hasher: &mut Fnv1aHasher, codes: &[i32]) {
    for code in codes {
        hasher.write_bytes(&code.to_le_bytes());
    }
}

fn append_env_pairs(hasher: &mut Fnv1aHasher, pairs: &[(&str, &str)]) {
    for (key, value) in pairs {
        append_str(hasher, key);
        append_str(hasher, value);
    }
}
