//! Pure policy for mcp-server dependency isolation checks.
//!
//! This module provides pure functions for verifying that `mcp-server` has no
//! transitive dependency on `ralph-workflow`. All logic here is side-effect-free
//! and operates on pre-parsed cargo metadata JSON values.

use std::collections::{HashMap, HashSet};

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
    packages
        .iter()
        .filter_map(|pkg| Some((pkg.get("name")?.as_str()?, pkg.get("id")?.as_str()?)))
        .fold(HashMap::new(), |name_to_ids, (name, id)| {
            let updated_ids = name_to_ids
                .get(name)
                .into_iter()
                .flat_map(|ids| ids.iter().copied())
                .chain(std::iter::once(id))
                .collect();

            name_to_ids
                .into_iter()
                .filter(|(existing_name, _)| *existing_name != name)
                .chain(std::iter::once((name, updated_ids)))
                .collect()
        })
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
    walk_dependency_graph(
        vec![mcp_server_id],
        [mcp_server_id].into_iter().collect(),
        mcp_server_id,
        id_to_name,
        id_to_deps,
        name_to_ids,
    )
}

fn walk_dependency_graph<'a>(
    pending_ids: Vec<&'a str>,
    visited_ids: HashSet<&'a str>,
    mcp_server_id: &'a str,
    id_to_name: &HashMap<&'a str, &'a str>,
    id_to_deps: &HashMap<&'a str, Vec<&'a str>>,
    name_to_ids: &HashMap<&'a str, Vec<&'a str>>,
) -> Option<String> {
    pending_ids
        .split_first()
        .and_then(|(current_id, remaining_ids)| {
            check_current_node(
                current_id,
                mcp_server_id,
                id_to_name,
                id_to_deps,
                name_to_ids,
            )
            .or_else(|| {
                let next_ids = dependency_ids_for(current_id, id_to_deps, name_to_ids);
                let unseen_ids: Vec<&str> = next_ids
                    .into_iter()
                    .filter(|dep_id| !visited_ids.contains(dep_id))
                    .collect();
                let next_pending_ids = remaining_ids
                    .iter()
                    .copied()
                    .chain(unseen_ids.iter().copied())
                    .collect();
                let next_visited_ids = visited_ids.iter().copied().chain(unseen_ids).collect();

                walk_dependency_graph(
                    next_pending_ids,
                    next_visited_ids,
                    mcp_server_id,
                    id_to_name,
                    id_to_deps,
                    name_to_ids,
                )
            })
        })
}

fn check_current_node<'a>(
    current_id: &'a str,
    mcp_server_id: &'a str,
    id_to_name: &HashMap<&'a str, &'a str>,
    id_to_deps: &HashMap<&'a str, Vec<&'a str>>,
    name_to_ids: &HashMap<&'a str, Vec<&'a str>>,
) -> Option<String> {
    if current_id == mcp_server_id {
        return dependency_names_for(current_id, id_to_deps)
            .into_iter()
            .find(|dep_name| *dep_name == "ralph-workflow")
            .map(|_| direct_violation_message());
    }

    id_to_name
        .get(current_id)
        .copied()
        .filter(|name| *name == "ralph-workflow")
        .map(|_| transitive_violation_message(current_id))
        .or_else(|| {
            let _ = name_to_ids;
            None
        })
}

fn dependency_names_for<'a>(
    package_id: &'a str,
    id_to_deps: &HashMap<&'a str, Vec<&'a str>>,
) -> Vec<&'a str> {
    id_to_deps.get(package_id).cloned().unwrap_or_default()
}

fn dependency_ids_for<'a>(
    package_id: &'a str,
    id_to_deps: &HashMap<&'a str, Vec<&'a str>>,
    name_to_ids: &HashMap<&'a str, Vec<&'a str>>,
) -> Vec<&'a str> {
    dependency_names_for(package_id, id_to_deps)
        .into_iter()
        .flat_map(|dep_name| {
            name_to_ids
                .get(dep_name)
                .into_iter()
                .flat_map(|dep_ids| dep_ids.iter().copied())
        })
        .collect()
}

fn direct_violation_message() -> String {
    "DEPENDENCY ISOLATION VIOLATION: mcp-server directly depends on \
     ralph-workflow. The dependency arrow must be strictly \
     one-directional: ralph-workflow \u{2192} mcp-server. \
     Remove ralph-workflow from mcp-server/Cargo.toml."
        .to_string()
}

fn transitive_violation_message(package_id: &str) -> String {
    format!(
        "DEPENDENCY ISOLATION VIOLATION: mcp-server transitively depends on \
         ralph-workflow (via package id: {package_id}). The dependency arrow must \
         be strictly one-directional: ralph-workflow \u{2192} mcp-server. \
         Audit mcp-server's dependency chain and remove the transitive path."
    )
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
