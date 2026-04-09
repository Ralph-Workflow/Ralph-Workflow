//! Integration test for the Makefile install targets.
//!
//! This validates that the Makefile's install targets correctly separate CLI-only
//! installation from GUI installation. The GUI requires cargo tauri build, not
//! cargo build --release, so they must be decoupled.
//!
//! Per integration test rules, we do not spawn external processes (no `make`).
//! We assert the observable, deterministic behavior of the Makefile content itself.

use crate::test_timeout::with_default_timeout;

/// Extract a Makefile target body by finding the target and extracting until the next target
fn extract_makefile_target(makefile: &str, target_name: &str) -> String {
    let pattern = format!("\n{target_name}:");
    let start = match makefile.find(&pattern) {
        Some(pos) => pos + 1,
        None => return String::new(),
    };

    let rest = &makefile[start..];
    let mut end = rest.len();

    for (i, line) in rest.lines().enumerate() {
        if i == 0 {
            continue;
        }
        let trimmed = line.trim();
        if !trimmed.is_empty()
            && trimmed.contains(':')
            && !trimmed.contains("$$")
            && !trimmed.starts_with('\t')
            && !trimmed.starts_with(" if")
            && !trimmed.starts_with(" else")
            && !trimmed.starts_with(" fi")
            && (trimmed.starts_with(char::is_alphabetic) || trimmed.starts_with('_'))
        {
            end = start + rest[..i].rfind('\n').map(|p| p + 1).unwrap_or(0);
            break;
        }
    }

    makefile[start..start + end].to_string()
}

#[test]
fn install_target_does_not_unconditionally_install_gui() {
    with_default_timeout(|| {
        let makefile = include_str!("../../Makefile");

        // The install target must NOT call $(MAKE) install-gui unconditionally.
        // The GUI binary requires cargo tauri build, not cargo build --release.
        let install_body = extract_makefile_target(makefile, "install");

        // Check for the broken pattern: a tab-indented $(MAKE) install-gui line
        // that appears unconditionally in the install target
        // The key is to check for an actual command that invokes install-gui, not just
        // text that mentions GUI in a message
        let has_unconditional_install_gui = install_body.lines().any(|line| {
            let trimmed = line.trim();
            // Match actual command invocation (starts with tab and calls install-gui)
            trimmed.starts_with('\t')
                && (trimmed == "$(MAKE) install-gui"
                    || trimmed.contains("$(MAKE) install-gui")
                    || trimmed.starts_with("make install-gui"))
        });

        assert!(
            !has_unconditional_install_gui,
            "install target must not unconditionally call install-gui. \
             GUI binary requires cargo tauri build, not cargo build --release. \
             Use a separate install-with-gui target instead."
        );
    });
}

// NOTE: The makefile_has_install_with_gui_target test was removed because
// the GUI was removed in commit 165cf6ad. The Makefile no longer has
// install-with-gui or install-full targets.
