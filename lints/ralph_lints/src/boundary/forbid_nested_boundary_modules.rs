//! Lint: `FORBID_NESTED_BOUNDARY_MODULES`
//!
//! Rejects nested modules inside boundary directories. Boundary modules
//! (`io/`, `runtime/`, `ffi/`, `boundary/`, `executor/`, `files/`, `git_helpers/`)
//! must be flat - they can contain files but not subdirectories with `mod.rs` or
//! `foo/bar.rs` module structures.
//!
//! ## FP principle: boundaries are leaves, not containers
//!
//! In Haskell, `IO` is a type tag (like `newtype IO a = IO (RealWorld -> (a, RealWorld))`).
//! It doesn't contain other modules - it's a boundary marker. The same applies here:
//! `io/` is where I/O happens, not a container for domain modules like `io/claude/`,
//! `io/opencode/`, etc.

use crate::domain::boundary::BOUNDARY_MODULES;
use rustc_ast::Crate;
use rustc_lint::{EarlyContext, EarlyLintPass, LintContext};
use rustc_session::{declare_lint, impl_lint_pass};
use rustc_span::{FileName, SourceFile, Span};

declare_lint! {
    /// ### What it does
    ///
    /// Detects when boundary module directories contain nested modules.
    ///
    /// Boundary modules must be flat directories. Nested submodules
    /// inside boundary directories are forbidden.
    ///
    /// ### Example (bad — nested module inside boundary)
    ///
    /// `io/claude/mod.rs` is a nested module inside boundary `io/`
    pub FORBID_NESTED_BOUNDARY_MODULES,
    Deny,
    "boundary modules (io/, runtime/, ffi/, boundary/, executor/, files/, git_helpers/) cannot contain nested submodules"
}

impl_lint_pass!(ForbidNestedBoundaryModules => [FORBID_NESTED_BOUNDARY_MODULES]);

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[FORBID_NESTED_BOUNDARY_MODULES]);
    lint_store.register_early_pass(|| Box::new(ForbidNestedBoundaryModules));
}

pub struct ForbidNestedBoundaryModules;

fn nested_boundary_help(boundary: &str, nested: &str) -> String {
    format!(
        "move `{nested}` outside `{boundary}` boundary - boundary modules are thin leaf adapters, not containers for deeper structure, because nested boundary trees tend to hide policy and parsing inside effectful code"
    )
}

impl ForbidNestedBoundaryModules {
    fn check_source_file(&self, cx: &EarlyContext<'_>, source_file: &SourceFile) {
        let FileName::Real(real_name) = &source_file.name else {
            return;
        };

        let Some(path) = real_name.local_path() else {
            return;
        };

        let violation = path_has_nested_boundary(path);

        if let Some((boundary, nested)) = violation {
            let warning_span = Span::with_root_ctxt(source_file.start_pos, source_file.start_pos);

            cx.span_lint(FORBID_NESTED_BOUNDARY_MODULES, warning_span, |diag| {
                diag.primary_message(format!(
                    "nested module `{}` inside boundary `{}`",
                    nested, boundary
                ));
                diag.help(nested_boundary_help(boundary, &nested));
                diag.note(
                    "boundary directories mark effect seams; if code needs internal submodule structure, move the real policy into non-boundary modules and keep the boundary flat and wiring-focused. See `docs/code-style/boundaries.md`.",
                );
            });
        }
    }
}

impl EarlyLintPass for ForbidNestedBoundaryModules {
    fn check_crate(&mut self, cx: &EarlyContext<'_>, _krate: &Crate) {
        let source_map = cx.sess().source_map();

        source_map.files().iter().for_each(|source_file| {
            self.check_source_file(cx, source_file);
        });
    }
}

/// Returns Some((boundary_name, nested_path)) if path contains a boundary with nested content.
fn path_has_nested_boundary(path: &std::path::Path) -> Option<(&'static str, String)> {
    let components: Vec<_> = path.components().collect();
    let len = components.len();

    components.iter().enumerate().find_map(|(i, component)| {
        let name = component.as_os_str().to_str()?;
        let stem = name.strip_suffix(".rs").unwrap_or(name);

        // Is this a boundary component?
        if let Some(&boundary) = BOUNDARY_MODULES.iter().find(|&&b| b == stem) {
            // Is there anything after it?
            if i + 1 < len {
                let remainder: String = components[(i + 1)..]
                    .iter()
                    .filter_map(|c| c.as_os_str().to_str())
                    .collect::<Vec<_>>()
                    .join("/");
                return Some((boundary, remainder));
            }
        }
        None
    })
}

#[cfg(test)]
mod tests {
    use super::{nested_boundary_help, path_has_nested_boundary};
    use std::path::Path;

    #[test]
    fn detects_nested_io() {
        let result = path_has_nested_boundary(Path::new("/src/io/claude/mod.rs"));
        assert!(result.is_some());
        let (b, n) = result.unwrap();
        assert_eq!(b, "io");
        assert_eq!(n, "claude/mod.rs");
    }

    #[test]
    fn allows_flat_io() {
        let result = path_has_nested_boundary(Path::new("/src/io/writer.rs"));
        assert!(result.is_none());
    }

    #[test]
    fn allows_io_mod_rs() {
        let result = path_has_nested_boundary(Path::new("/src/io/mod.rs"));
        assert!(result.is_none());
    }

    #[test]
    fn allows_sibling_json_parser() {
        let result = path_has_nested_boundary(Path::new("/src/json_parser/claude/mod.rs"));
        assert!(result.is_none());
    }

    #[test]
    fn detects_nested_runtime() {
        let result = path_has_nested_boundary(Path::new("/src/runtime/process/mod.rs"));
        assert!(result.is_some());
        let (b, n) = result.unwrap();
        assert_eq!(b, "runtime");
        assert_eq!(n, "process/mod.rs");
    }

    #[test]
    fn help_explains_why_boundaries_stay_flat() {
        let help = nested_boundary_help("io", "provider/mod.rs");

        assert!(help.contains("thin leaf adapters"));
        assert!(help.contains("hide policy"));
    }
}
