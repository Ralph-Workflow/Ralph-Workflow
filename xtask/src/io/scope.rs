//! Scope definitions for check caching.
//!
//! Defines which files/directories are relevant for each check, enabling
//! content-addressed caching that only re-runs checks when their inputs change.

/// Scope definition: which directories/glob patterns constitute the
/// relevant input for a given check name.
#[derive(Clone, Copy)]
pub struct ScopeGlob {
    pub dir: &'static str,
    pub pattern: &'static str,
}

pub enum CheckScope {
    /// Hash all .rs files under the given directory paths.
    Directories(&'static [&'static str]),
    /// Hash Cargo.lock plus all .rs files under the given paths.
    Build(&'static [&'static str]),
    /// Hash a build scope plus additional non-Rust files or directories watched at compile time.
    BuildWithExtras {
        dirs: &'static [&'static str],
        globs: &'static [ScopeGlob],
        files: &'static [&'static str],
    },
    /// Hash explicitly selected files and globbed inputs.
    Patterns {
        globs: &'static [ScopeGlob],
        files: &'static [&'static str],
        include_lock: bool,
    },
}

const RALPH_GUI_RUST_SCOPE_DIRS: &[&str] = &["ralph-gui/src", "ralph-workflow/src"];
const RALPH_GUI_BUILD_EXTRA_GLOBS: &[ScopeGlob] = &[
    ScopeGlob {
        dir: "ralph-gui/capabilities",
        pattern: "*",
    },
    ScopeGlob {
        dir: "ralph-gui/icons",
        pattern: "*",
    },
];
const RALPH_GUI_BUILD_EXTRA_FILES: &[&str] = &["ralph-gui/build.rs", "ralph-gui/tauri.conf.json"];
const FMT_CHECK_SCOPE_GLOBS: &[ScopeGlob] = &[
    ScopeGlob {
        dir: "ralph-workflow/src",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "tests",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "xtask/src",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "test-helpers/src",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "ralph-gui/src",
        pattern: "*.rs",
    },
];
const FMT_CHECK_SCOPE_FILES: &[&str] = &["ralph-gui/build.rs"];
const RALPH_WORKFLOW_COMPILE_TIME_EXTRA_GLOBS: &[ScopeGlob] = &[
    ScopeGlob {
        dir: "templates/prompts",
        pattern: "*",
    },
    ScopeGlob {
        dir: "ralph-workflow/src/prompts/templates",
        pattern: "*",
    },
    ScopeGlob {
        dir: "ralph-workflow/src/files/llm_output_extraction",
        pattern: "*",
    },
];
const RALPH_WORKFLOW_COMPILE_TIME_EXTRA_FILES: &[&str] = &[];
const INTEGRATION_TEST_AND_RALPH_WORKFLOW_EXTRA_GLOBS: &[ScopeGlob] = &[
    ScopeGlob {
        dir: "tests/integration_tests/artifacts",
        pattern: "*",
    },
    ScopeGlob {
        dir: "templates/prompts",
        pattern: "*",
    },
    ScopeGlob {
        dir: "ralph-workflow/src/prompts/templates",
        pattern: "*",
    },
    ScopeGlob {
        dir: "ralph-workflow/src/files/llm_output_extraction",
        pattern: "*",
    },
];
const DYLINT_SCOPE_GLOBS: &[ScopeGlob] = &[
    ScopeGlob {
        dir: "ralph-workflow/src",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "lints/ralph_lints/src",
        pattern: "*.rs",
    },
];
const DYLINT_SCOPE_FILES: &[&str] = &[
    "Cargo.toml",
    "Cargo.lock",
    "Makefile",
    "rust-toolchain.toml",
    "rust-toolchain",
    ".cargo/config.toml",
    ".cargo/config",
    "clippy.toml",
    "lints/ralph_lints/Cargo.toml",
    "lints/ralph_lints/Cargo.lock",
    "lints/ralph_lints/.cargo/config.toml",
    "lints/ralph_lints/rust-toolchain.toml",
    "lints/ralph_lints/dylint-link",
    "lints/ralph_lints/rustc-nightly",
];
const RALPH_GUI_FRONTEND_INSTALL_FILES: &[&str] =
    &["ralph-gui/ui/package.json", "ralph-gui/ui/bun.lock"];
const RALPH_GUI_FRONTEND_CHECK_FILES: &[&str] = &[
    "ralph-gui/ui/package.json",
    "ralph-gui/ui/bun.lock",
    "ralph-gui/ui/tsconfig.json",
    "ralph-gui/ui/tsconfig.node.json",
    "ralph-gui/ui/vite.config.ts",
    "ralph-gui/ui/eslint.config.mjs",
    "ralph-gui/ui/index.html",
];
const RALPH_GUI_FRONTEND_SRC_GLOBS: &[ScopeGlob] = &[ScopeGlob {
    dir: "ralph-gui/ui/src",
    pattern: "*",
}];
const FORBIDDEN_ALLOW_EXPECT_SCOPE_GLOBS: &[ScopeGlob] = &[
    ScopeGlob {
        dir: "ralph-workflow/src",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "tests",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "xtask/src",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "test-helpers/src",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "ralph-gui/src",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "lints",
        pattern: "*.rs",
    },
];
const FORBIDDEN_ALLOW_EXPECT_SCOPE_FILES: &[&str] = &["ralph-gui/build.rs"];
pub const SCOPE_HASH_VERSION: &[u8] = b"scope-v2";
pub const NATIVE_SCAN_HASH_VERSION: &[u8] = b"native-scan-v2";
pub const NATIVE_REQUIRED_HASH_VERSION: &[u8] = b"native-required-v1";
pub const COMMAND_DEFINITION_HASH_VERSION: &[u8] = b"command-definition-v1";

/// Returns a stable string key for a scope, used for in-process memoization.
/// The key encodes both the scope type (directories vs build) and the directory list.
pub fn scope_memo_key(scope: &CheckScope) -> String {
    match scope {
        CheckScope::Directories(dirs) => format!("d:{}", dirs.join(",")),
        CheckScope::Build(dirs) => format!("b:{}", dirs.join(",")),
        CheckScope::BuildWithExtras { dirs, globs, files } => {
            let glob_key = globs
                .iter()
                .map(|glob| format!("{}@{}", glob.dir, glob.pattern))
                .collect::<Vec<_>>()
                .join(",");
            format!("bx:{}:{glob_key}:{}", dirs.join(","), files.join(","))
        }
        CheckScope::Patterns {
            globs,
            files,
            include_lock,
        } => {
            let glob_key = globs
                .iter()
                .map(|glob| format!("{}@{}", glob.dir, glob.pattern))
                .collect::<Vec<_>>()
                .join(",");
            format!("p:{include_lock}:{glob_key}:{}", files.join(","))
        }
    }
}

/// Returns the scope for a given check name. Checks not listed here
/// are assumed to have Build scope (most conservative: any change triggers re-run).
pub fn scope_for(check_name: &str) -> CheckScope {
    scope_for_special_checks(check_name)
        .or_else(|| scope_for_clippy_checks(check_name))
        .or_else(|| scope_for_gui_checks(check_name))
        .or_else(|| scope_for_integration_checks(check_name))
        .unwrap_or(CheckScope::Build(&[
            "ralph-workflow/src",
            "tests",
            "xtask/src",
        ]))
}

fn scope_for_special_checks(check_name: &str) -> Option<CheckScope> {
    match check_name {
        "audit-ignore-has-url" => Some(CheckScope::Directories(&["tests", "ralph-workflow/src"])),
        "forbidden-allow-expect-scan" => Some(CheckScope::Patterns {
            globs: FORBIDDEN_ALLOW_EXPECT_SCOPE_GLOBS,
            files: FORBIDDEN_ALLOW_EXPECT_SCOPE_FILES,
            include_lock: false,
        }),
        "fmt-check" => Some(CheckScope::Patterns {
            globs: FMT_CHECK_SCOPE_GLOBS,
            files: FMT_CHECK_SCOPE_FILES,
            include_lock: false,
        }),
        "dylint" => Some(CheckScope::Patterns {
            globs: DYLINT_SCOPE_GLOBS,
            files: DYLINT_SCOPE_FILES,
            include_lock: false,
        }),
        _ => None,
    }
}

fn scope_for_clippy_checks(check_name: &str) -> Option<CheckScope> {
    match check_name {
        "clippy-core" => Some(CheckScope::BuildWithExtras {
            dirs: &["ralph-workflow/src", "tests", "test-helpers/src"],
            globs: RALPH_WORKFLOW_COMPILE_TIME_EXTRA_GLOBS,
            files: RALPH_WORKFLOW_COMPILE_TIME_EXTRA_FILES,
        }),
        "test-ralph-workflow-lib" => Some(CheckScope::BuildWithExtras {
            dirs: &["ralph-workflow/src"],
            globs: RALPH_WORKFLOW_COMPILE_TIME_EXTRA_GLOBS,
            files: RALPH_WORKFLOW_COMPILE_TIME_EXTRA_FILES,
        }),
        "clippy-xtask" | "test-xtask" => Some(CheckScope::Build(&["xtask/src"])),
        _ => None,
    }
}

fn scope_for_gui_checks(check_name: &str) -> Option<CheckScope> {
    match check_name {
        "clippy-ralph-gui" | "test-ralph-gui-lib" => Some(CheckScope::BuildWithExtras {
            dirs: RALPH_GUI_RUST_SCOPE_DIRS,
            globs: RALPH_GUI_BUILD_EXTRA_GLOBS,
            files: RALPH_GUI_BUILD_EXTRA_FILES,
        }),
        "ralph-gui-frontend-install" => Some(CheckScope::Patterns {
            globs: &[],
            files: RALPH_GUI_FRONTEND_INSTALL_FILES,
            include_lock: false,
        }),
        "ralph-gui-frontend-lint" | "ralph-gui-frontend-test" => Some(CheckScope::Patterns {
            globs: RALPH_GUI_FRONTEND_SRC_GLOBS,
            files: RALPH_GUI_FRONTEND_CHECK_FILES,
            include_lock: false,
        }),
        _ => None,
    }
}

fn scope_for_integration_checks(check_name: &str) -> Option<CheckScope> {
    match check_name {
        "test-integration" => Some(CheckScope::BuildWithExtras {
            dirs: &[
                "ralph-workflow/src",
                "tests/integration_tests",
                "test-helpers/src",
            ],
            globs: INTEGRATION_TEST_AND_RALPH_WORKFLOW_EXTRA_GLOBS,
            files: RALPH_WORKFLOW_COMPILE_TIME_EXTRA_FILES,
        }),
        "release-build" => Some(CheckScope::BuildWithExtras {
            dirs: &["ralph-workflow/src", "test-helpers/src", "xtask/src"],
            globs: RALPH_WORKFLOW_COMPILE_TIME_EXTRA_GLOBS,
            files: RALPH_WORKFLOW_COMPILE_TIME_EXTRA_FILES,
        }),
        _ => None,
    }
}

pub fn native_required_scope_for(check_name: &str) -> CheckScope {
    match check_name {
        "compliance-timeout-wrapper" => CheckScope::Patterns {
            globs: &[ScopeGlob {
                dir: "tests/integration_tests",
                pattern: "*.rs",
            }],
            files: &[],
            include_lock: false,
        },
        "audit-no-shell-scripts" => CheckScope::Patterns {
            globs: &[
                ScopeGlob {
                    dir: "scripts",
                    pattern: "*.sh",
                },
                ScopeGlob {
                    dir: "tests/integration_tests",
                    pattern: "*.sh",
                },
            ],
            files: &[],
            include_lock: false,
        },
        _ => CheckScope::Build(&["tests", "xtask/src"]),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_scope_for_clippy_core_is_granular() {
        match scope_for("clippy-core") {
            CheckScope::BuildWithExtras { dirs, globs, files } => {
                assert_eq!(dirs, &["ralph-workflow/src", "tests", "test-helpers/src"]);
                assert!(
                    globs
                        .iter()
                        .any(|glob| glob.dir == "templates/prompts" && glob.pattern == "*"),
                    "clippy-core must track embedded prompt markdown files consumed by ralph-workflow"
                );
                assert!(
                    globs.iter().any(|glob| {
                        glob.dir == "ralph-workflow/src/prompts/templates" && glob.pattern == "*"
                    }),
                    "clippy-core must track embedded prompt template text files consumed by ralph-workflow"
                );
                assert!(
                    globs.iter().any(|glob| {
                        glob.dir == "ralph-workflow/src/files/llm_output_extraction"
                            && glob.pattern == "*"
                    }),
                    "clippy-core must track embedded XSD files consumed by ralph-workflow"
                );
                assert!(
                    files.is_empty(),
                    "clippy-core should track compile-time resources via directory extras"
                );
            }
            CheckScope::Directories(_) | CheckScope::Build(_) | CheckScope::Patterns { .. } => {
                panic!("clippy-core must use BuildWithExtras scope")
            }
        }
    }

    #[test]
    fn test_scope_for_clippy_xtask_is_granular() {
        let key = scope_memo_key(&scope_for("clippy-xtask"));
        assert_eq!(key, "b:xtask/src");
    }

    #[test]
    fn test_scope_for_test_xtask_is_granular() {
        let key = scope_memo_key(&scope_for("test-xtask"));
        assert_eq!(key, "b:xtask/src");
    }

    #[test]
    fn test_scope_memo_key_is_stable() {
        let k1 = scope_memo_key(&CheckScope::Build(&["ralph-workflow/src"]));
        let k2 = scope_memo_key(&CheckScope::Build(&["ralph-workflow/src"]));
        assert_eq!(k1, k2);

        let k3 = scope_memo_key(&CheckScope::Directories(&["ralph-workflow/src"]));
        assert_ne!(
            k1, k3,
            "Build and Directories keys for same dirs must differ"
        );
    }
}
