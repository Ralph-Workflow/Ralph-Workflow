//! Boundary checks for workspace structural invariants.
//!
//! These checks enforce cross-crate architectural constraints that cannot be
//! expressed as Rust type-system rules. Each check runs as a native xtask
//! verification step and must pass with zero errors before any PR is merged.

use crate::domain::isolation_policy;
use crate::runtime::verify::{CheckStatus, NativeCheckResult};
use std::path::Path;
use std::process::Command;

/// Verify that `mcp-server` has no transitive dependency on `ralph-workflow`.
///
/// # Architectural Invariant
///
/// `mcp-server` must be usable without `ralph-workflow`. A third-party application
/// must be able to depend on `mcp-server` directly and host a fully functional MCP
/// server without ever importing or knowing about `ralph-workflow`.
///
/// The dependency arrow is strictly one-directional: `ralph-workflow` → `mcp-server`.
/// Any reversal of this arrow is a build-time architectural violation.
///
/// # Check Method
///
/// Runs `cargo metadata --format-version 1` and parses the JSON output to walk
/// `mcp-server`'s transitive dependency graph. Returns `Error` if `ralph-workflow`
/// appears anywhere in that graph.
///
/// Returns `Pass` when no `Cargo.toml` is present at `repo_root` (e.g. in unit-test
/// environments with a fake repo path — same convention as other native checks).
pub fn check_mcp_server_no_ralph_workflow_dependency(repo_root: &Path) -> NativeCheckResult {
    // Skip check in environments without a real workspace (e.g. unit test fake paths).
    if !repo_root.join("Cargo.toml").exists() {
        return NativeCheckResult {
            status: CheckStatus::Pass,
            message: String::new(),
        };
    }

    let metadata_json = match run_cargo_metadata(repo_root) {
        Ok(json) => json,
        Err(e) => {
            return NativeCheckResult {
                status: CheckStatus::Error,
                message: format!("Failed to run cargo metadata: {e}"),
            }
        }
    };

    check_isolation_from_json(&metadata_json)
}

/// Run `cargo metadata --format-version 1` from the repo root directory.
fn run_cargo_metadata(repo_root: &Path) -> Result<String, String> {
    let output = Command::new("cargo")
        .args(["metadata", "--format-version", "1"])
        .current_dir(repo_root)
        .output()
        .map_err(|e| format!("cargo metadata spawn failed: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("cargo metadata failed: {stderr}"));
    }

    String::from_utf8(output.stdout).map_err(|e| format!("cargo metadata output not UTF-8: {e}"))
}

/// Parse cargo metadata JSON and check mcp-server isolation.
///
/// Thin boundary wiring: parses JSON, extracts packages array, delegates
/// to pure domain policy, translates result.
fn check_isolation_from_json(metadata_json: &str) -> NativeCheckResult {
    let metadata: serde_json::Value = match serde_json::from_str(metadata_json) {
        Ok(v) => v,
        Err(e) => {
            return NativeCheckResult {
                status: CheckStatus::Error,
                message: format!("Failed to parse cargo metadata JSON: {e}"),
            }
        }
    };

    let packages = match metadata.get("packages").and_then(|p| p.as_array()) {
        Some(pkgs) => pkgs,
        None => {
            return NativeCheckResult {
                status: CheckStatus::Error,
                message: "cargo metadata missing 'packages' array".to_string(),
            }
        }
    };

    match isolation_policy::check_mcp_server_isolation_from_packages(packages) {
        None => NativeCheckResult {
            status: CheckStatus::Pass,
            message: String::new(),
        },
        Some(msg) => NativeCheckResult {
            status: CheckStatus::Error,
            message: msg,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_package(id: &str, name: &str, deps: &[(&str, &str)]) -> serde_json::Value {
        let dep_array: Vec<serde_json::Value> = deps
            .iter()
            .map(|(dep_name, _dep_id)| {
                serde_json::json!({
                    "name": dep_name,
                    "kind": null,
                    "target": null,
                    "optional": false,
                    "uses_default_features": true,
                    "features": []
                })
            })
            .collect();

        serde_json::json!({
            "id": id,
            "name": name,
            "version": "0.1.0",
            "source": null,
            "dependencies": dep_array,
            "targets": [],
            "features": {},
            "manifest_path": format!("{}/Cargo.toml", name),
            "metadata": null,
            "publish": null,
            "authors": [],
            "categories": [],
            "keywords": [],
            "readme": null,
            "repository": null,
            "homepage": null,
            "documentation": null,
            "edition": "2021",
            "links": null
        })
    }

    fn make_metadata(packages: Vec<serde_json::Value>) -> serde_json::Value {
        serde_json::json!({
            "packages": packages,
            "workspace_members": [],
            "resolve": null,
            "target_directory": "/tmp/target",
            "version": 1,
            "workspace_root": "/tmp"
        })
    }

    #[test]
    fn passes_when_mcp_server_not_in_workspace() {
        let metadata = make_metadata(vec![make_package("rw-1.0.0", "ralph-workflow", &[])]);
        let result = check_isolation_from_json(&serde_json::to_string(&metadata).unwrap());
        assert_eq!(result.status, CheckStatus::Pass);
    }

    #[test]
    fn passes_when_mcp_server_has_no_ralph_workflow_dep() {
        let metadata = make_metadata(vec![
            make_package("mcp-1.0.0", "mcp-server", &[("serde", "serde-1.0.0")]),
            make_package("serde-1.0.0", "serde", &[]),
            make_package("rw-1.0.0", "ralph-workflow", &[("mcp-server", "mcp-1.0.0")]),
        ]);
        let result = check_isolation_from_json(&serde_json::to_string(&metadata).unwrap());
        assert_eq!(result.status, CheckStatus::Pass);
    }

    #[test]
    fn errors_when_mcp_server_directly_depends_on_ralph_workflow() {
        let metadata = make_metadata(vec![
            make_package("mcp-1.0.0", "mcp-server", &[("ralph-workflow", "rw-1.0.0")]),
            make_package("rw-1.0.0", "ralph-workflow", &[]),
        ]);
        let result = check_isolation_from_json(&serde_json::to_string(&metadata).unwrap());
        assert_eq!(result.status, CheckStatus::Error);
        assert!(result.message.contains("DEPENDENCY ISOLATION VIOLATION"));
    }

    #[test]
    fn errors_when_mcp_server_transitively_depends_on_ralph_workflow() {
        // mcp-server → middle → ralph-workflow
        let metadata = make_metadata(vec![
            make_package("mcp-1.0.0", "mcp-server", &[("middle", "middle-1.0.0")]),
            make_package("middle-1.0.0", "middle", &[("ralph-workflow", "rw-1.0.0")]),
            make_package("rw-1.0.0", "ralph-workflow", &[]),
        ]);
        let result = check_isolation_from_json(&serde_json::to_string(&metadata).unwrap());
        assert_eq!(result.status, CheckStatus::Error);
        assert!(result.message.contains("DEPENDENCY ISOLATION VIOLATION"));
    }

    #[test]
    fn passes_with_invalid_json() {
        let result = check_isolation_from_json("not valid json {{{");
        assert_eq!(result.status, CheckStatus::Error);
        assert!(result.message.contains("parse cargo metadata JSON"));
    }
}
