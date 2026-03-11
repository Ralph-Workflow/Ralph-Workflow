// Lint levels are configured in Cargo.toml [lints] to allow crate-specific
// exemptions (e.g. needless_pass_by_value is required for tauri::State params).

pub mod commands;
pub mod state;

#[cfg(test)]
mod tests {
    #[test]
    fn build_script_declares_explicit_tauri_rerun_inputs() {
        let build_script = include_str!("../build.rs");

        for watched_path in ["tauri.conf.json", "capabilities", "icons"] {
            assert!(
                build_script.contains(&format!("cargo:rerun-if-changed={watched_path}")),
                "build.rs must declare rerun-if-changed for {watched_path}"
            );
        }
    }

    #[test]
    fn cargo_manifest_excludes_transient_ui_directories_from_package_scan() {
        let cargo_toml = include_str!("../Cargo.toml");

        for excluded_path in ["ui/node_modules/**", "ui/dist/**"] {
            assert!(
                cargo_toml.contains(excluded_path),
                "Cargo.toml must exclude {excluded_path} so cargo fingerprinting does not race frontend installs"
            );
        }
    }
}
