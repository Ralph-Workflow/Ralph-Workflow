//! Pure policy for mcp-server dependency isolation checks.
//!
//! This module provides pure functions for verifying that `mcp-server` has no
//! transitive dependency on `ralph-workflow`. All logic here is side-effect-free
//! and operates on pre-parsed cargo metadata JSON values.

use std::collections::{HashMap, HashSet, VecDeque};

/// Check whether `mcp-server` transitively depends on `ralph-workflow`.
///
/// Takes the `packages` array from `cargo metadata --format-version 1` output.
///
/// Returns `Some(error_message)` if a violation is found, `None` if isolation holds.
pub fn check_mcp_server_isolation_from_packages(packages: &[serde_json::Value]) -> Option<String> {
    let id_to_name = build_id_to_name(packages);
    let id_to_deps = build_id_to_deps(packages);
    let name_to_ids = build_name_to_ids(packages);
    let mcp_server_id = find_package_id_by_name(packages, "mcp-server")?;
    check_isolation(mcp_server_id, &id_to_name, &id_to_deps, &name_to_ids)
}

/// Build a map from package id → package name.
pub fn build_id_to_name(packages: &[serde_json::Value]) -> HashMap<&str, &str> {
    packages
        .iter()
        .filter_map(|pkg| {
            let id = pkg.get("id")?.as_str()?;
            let name = pkg.get("name")?.as_str()?;
            Some((id, name))
        })
        .collect()
}

/// Build a map from package id → list of direct dependency names.
///
/// Dependency names are as they appear in the package's `dependencies` array.
pub fn build_id_to_deps(packages: &[serde_json::Value]) -> HashMap<&str, Vec<&str>> {
    packages
        .iter()
        .filter_map(|pkg| {
            let id = pkg.get("id")?.as_str()?;
            let deps = pkg.get("dependencies")?.as_array()?;
            let dep_names: Vec<&str> = deps
                .iter()
                .filter_map(|dep| dep.get("name")?.as_str())
                .collect();
            Some((id, dep_names))
        })
        .collect()
}

/// Build a map from package name → list of package ids with that name.
///
/// Multiple ids may share a name when different versions of the same crate
/// are present in the workspace (e.g. serde 1.0.0 and serde 2.0.0).
pub fn build_name_to_ids(packages: &[serde_json::Value]) -> HashMap<&str, Vec<&str>> {
    let mut map: HashMap<&str, Vec<&str>> = HashMap::new();
    for pkg in packages {
        if let (Some(name), Some(id)) = (
            pkg.get("name").and_then(|n| n.as_str()),
            pkg.get("id").and_then(|i| i.as_str()),
        ) {
            map.entry(name).or_default().push(id);
        }
    }
    map
}

/// Find the package id for a package with the given name.
///
/// Returns `None` if no package with that name exists in `packages`.
pub fn find_package_id_by_name<'a>(
    packages: &'a [serde_json::Value],
    name: &str,
) -> Option<&'a str> {
    packages.iter().find_map(|pkg| {
        let pkg_name = pkg.get("name")?.as_str()?;
        if pkg_name == name {
            pkg.get("id")?.as_str()
        } else {
            None
        }
    })
}

/// BFS over `mcp_server_id`'s transitive dependency graph looking for `ralph-workflow`.
///
/// Returns `Some(error_message)` if `ralph-workflow` is reachable, `None` otherwise.
pub fn check_isolation<'a>(
    mcp_server_id: &'a str,
    id_to_name: &HashMap<&'a str, &'a str>,
    id_to_deps: &HashMap<&'a str, Vec<&'a str>>,
    name_to_ids: &HashMap<&'a str, Vec<&'a str>>,
) -> Option<String> {
    let mut visited: HashSet<&str> = HashSet::new();
    let mut queue: VecDeque<&str> = VecDeque::new();
    queue.push_back(mcp_server_id);
    visited.insert(mcp_server_id);

    while let Some(current_id) = queue.pop_front() {
        if current_id == mcp_server_id {
            if let Some(msg) = check_direct_deps(
                mcp_server_id,
                id_to_deps,
                name_to_ids,
                &mut visited,
                &mut queue,
            ) {
                return Some(msg);
            }
            continue;
        }

        if let Some(msg) = check_transitive_dep(
            current_id,
            id_to_name,
            id_to_deps,
            name_to_ids,
            &mut visited,
            &mut queue,
        ) {
            return Some(msg);
        }
    }

    None
}

/// Check direct dependencies of mcp-server for ralph-workflow.
///
/// Returns Some(error) if ralph-workflow is a direct dep, enqueues others for BFS.
fn check_direct_deps<'a>(
    pkg_id: &'a str,
    id_to_deps: &HashMap<&'a str, Vec<&'a str>>,
    name_to_ids: &HashMap<&'a str, Vec<&'a str>>,
    visited: &mut HashSet<&'a str>,
    queue: &mut VecDeque<&'a str>,
) -> Option<String> {
    let dep_names = id_to_deps.get(pkg_id)?;
    for &dep_name in dep_names {
        if dep_name == "ralph-workflow" {
            return Some(
                "DEPENDENCY ISOLATION VIOLATION: mcp-server directly depends on \
                 ralph-workflow. The dependency arrow must be strictly \
                 one-directional: ralph-workflow \u{2192} mcp-server. \
                 Remove ralph-workflow from mcp-server/Cargo.toml."
                    .to_string(),
            );
        }
        enqueue_by_name(dep_name, name_to_ids, visited, queue);
    }
    None
}

/// Check a transitive dependency node for ralph-workflow.
///
/// Returns Some(error) if this node is ralph-workflow, enqueues its deps.
fn check_transitive_dep<'a>(
    current_id: &'a str,
    id_to_name: &HashMap<&'a str, &'a str>,
    id_to_deps: &HashMap<&'a str, Vec<&'a str>>,
    name_to_ids: &HashMap<&'a str, Vec<&'a str>>,
    visited: &mut HashSet<&'a str>,
    queue: &mut VecDeque<&'a str>,
) -> Option<String> {
    if let Some(&name) = id_to_name.get(current_id) {
        if name == "ralph-workflow" {
            return Some(format!(
                "DEPENDENCY ISOLATION VIOLATION: mcp-server transitively depends on \
                 ralph-workflow (via package id: {current_id}). The dependency arrow must \
                 be strictly one-directional: ralph-workflow \u{2192} mcp-server. \
                 Audit mcp-server's dependency chain and remove the transitive path."
            ));
        }
    }

    if let Some(dep_names) = id_to_deps.get(current_id) {
        for &dep_name in dep_names {
            enqueue_by_name(dep_name, name_to_ids, visited, queue);
        }
    }

    None
}

/// Resolve a dependency name to package ids and enqueue unvisited ones.
fn enqueue_by_name<'a>(
    dep_name: &'a str,
    name_to_ids: &HashMap<&'a str, Vec<&'a str>>,
    visited: &mut HashSet<&'a str>,
    queue: &mut VecDeque<&'a str>,
) {
    if let Some(dep_ids) = name_to_ids.get(dep_name) {
        for &dep_id in dep_ids {
            if visited.insert(dep_id) {
                queue.push_back(dep_id);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_pkg(id: &str, name: &str, deps: &[&str]) -> serde_json::Value {
        let dep_array: Vec<serde_json::Value> = deps
            .iter()
            .map(|dep_name| serde_json::json!({ "name": dep_name }))
            .collect();
        serde_json::json!({
            "id": id,
            "name": name,
            "dependencies": dep_array
        })
    }

    #[test]
    fn passes_when_mcp_server_not_in_workspace() {
        let packages = vec![make_pkg("rw-1.0", "ralph-workflow", &[])];
        assert!(check_mcp_server_isolation_from_packages(&packages).is_none());
    }

    #[test]
    fn passes_when_mcp_server_has_no_ralph_workflow_dep() {
        let packages = vec![
            make_pkg("mcp-1.0", "mcp-server", &["serde"]),
            make_pkg("serde-1.0", "serde", &[]),
            make_pkg("rw-1.0", "ralph-workflow", &["mcp-server"]),
        ];
        assert!(check_mcp_server_isolation_from_packages(&packages).is_none());
    }

    #[test]
    fn errors_when_mcp_server_directly_depends_on_ralph_workflow() {
        let packages = vec![
            make_pkg("mcp-1.0", "mcp-server", &["ralph-workflow"]),
            make_pkg("rw-1.0", "ralph-workflow", &[]),
        ];
        let result = check_mcp_server_isolation_from_packages(&packages);
        assert!(result.is_some());
        assert!(result.unwrap().contains("DEPENDENCY ISOLATION VIOLATION"));
    }

    #[test]
    fn errors_when_mcp_server_transitively_depends_on_ralph_workflow() {
        let packages = vec![
            make_pkg("mcp-1.0", "mcp-server", &["middle"]),
            make_pkg("middle-1.0", "middle", &["ralph-workflow"]),
            make_pkg("rw-1.0", "ralph-workflow", &[]),
        ];
        let result = check_mcp_server_isolation_from_packages(&packages);
        assert!(result.is_some());
        assert!(result.unwrap().contains("DEPENDENCY ISOLATION VIOLATION"));
    }

    #[test]
    fn find_package_id_by_name_returns_none_for_missing() {
        let packages = vec![make_pkg("rw-1.0", "ralph-workflow", &[])];
        assert!(find_package_id_by_name(&packages, "mcp-server").is_none());
    }

    #[test]
    fn find_package_id_by_name_returns_id_for_present() {
        let packages = vec![make_pkg("mcp-1.0", "mcp-server", &[])];
        assert_eq!(
            find_package_id_by_name(&packages, "mcp-server"),
            Some("mcp-1.0")
        );
    }
}
