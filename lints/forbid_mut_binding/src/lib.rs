#![feature(rustc_private)]
// ── Lint policy ──
// This rule enforces a functional programming principle.  The rule itself
// (what it forbids, where it permits exceptions) MUST NOT be altered.
// If the *implementation* has a bug — false positives, false negatives,
// or code that contradicts the principle it enforces — fix the
// implementation.  The spirit of the rule is authoritative, not the
// current code.
#![deny(warnings)]
#![deny(clippy::all)]

extern crate rustc_ast;
extern crate rustc_span;

use rustc_ast::ast::{BindingMode, Mutability, Pat, PatKind};
use rustc_lint::{EarlyContext, EarlyLintPass, LintContext};
use rustc_span::FileName;
use std::path::{Component, Path};

/// Boundary module path components where `let mut` is permitted.
///
/// Code inside directories whose final component matches one of these names
/// is considered a boundary module and may use mutable bindings.
const BOUNDARY_MODULES: &[&str] = &["io", "runtime", "ffi", "boundary"];

dylint_linting::impl_early_lint! {
    /// ### What it does
    ///
    /// Rejects mutable bindings (`let mut`, mutable function parameters)
    /// outside of explicitly whitelisted boundary modules (`io/`, `runtime/`,
    /// `ffi/`, `boundary/`).
    ///
    /// ### FP principle: immutability by default
    ///
    /// In Haskell every binding is immutable — there is no `let mut`.
    /// Values are transformed by producing new values, not by mutating
    /// existing ones.  This makes code referentially transparent: you
    /// can substitute a binding with its definition anywhere without
    /// changing the program's meaning.
    ///
    /// In Rust, mutable bindings break that property.  They encourage
    /// imperative accumulation patterns that are harder to reason about,
    /// test, and compose.  Prefer `fold`, `map`, `collect`, and other
    /// combinator-based transformations that return new values.
    ///
    /// ### Boundary exceptions
    ///
    /// Boundary code (I/O, FFI, runtime glue) often must interact with
    /// APIs that require mutation.  Place such code in a module whose
    /// path contains one of the boundary markers.  This mirrors the
    /// Haskell separation between pure code and the `IO` monad.
    ///
    /// ### Example (bad — mutable accumulator)
    ///
    /// ```rust,ignore
    /// let mut total = 0;
    /// for item in items {
    ///     total += item.price;
    /// }
    /// ```
    ///
    /// ### Example (good — immutable fold)
    ///
    /// ```rust,ignore
    /// let total: u64 = items.iter().map(|item| item.price).sum();
    /// ```
    pub FORBID_MUT_BINDING,
    Deny,
    "`let mut` bindings are forbidden outside boundary modules",
    ForbidMutBinding
}

#[derive(Default)]
pub struct ForbidMutBinding;

fn path_contains_boundary_component(path: &Path) -> bool {
    path.components().any(|component| match component {
        Component::Normal(name) => {
            let name_str = name.to_str().unwrap_or("");
            // Check directory name (strip .rs extension for file-level modules)
            let stem = name_str.strip_suffix(".rs").unwrap_or(name_str);
            BOUNDARY_MODULES.iter().any(|b| *b == stem)
        }
        _ => false,
    })
}

fn is_in_boundary_module(cx: &EarlyContext<'_>, span: rustc_span::Span) -> bool {
    let source_map = cx.sess().source_map();
    let filename = source_map.span_to_filename(span);
    match &filename {
        FileName::Real(real_name) => {
            if let Some(path) = real_name.local_path() {
                path_contains_boundary_component(path)
            } else {
                false
            }
        }
        _ => false,
    }
}

impl EarlyLintPass for ForbidMutBinding {
    fn check_pat(&mut self, cx: &EarlyContext<'_>, pat: &Pat) {
        if let PatKind::Ident(BindingMode(_, Mutability::Mut), ident, _) = &pat.kind {
            if is_in_boundary_module(cx, pat.span) {
                return;
            }

            cx.span_lint(FORBID_MUT_BINDING, pat.span, |diag| {
                diag.primary_message(format!(
                    "`let mut {}` is forbidden outside boundary modules",
                    ident.name
                ));
                diag.help("replace mutable accumulator with iterator pipeline: `xs.iter().map(f).collect()` or `xs.iter().fold(init, f)`. See quick reference in `docs/code-style/functional-transformations.md`.");
                diag.note(
                    "if mutation is genuinely required for I/O or FFI, move this code into a \
                     boundary module (io/, runtime/, ffi/, boundary/)",
                );
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::path_contains_boundary_component;
    use std::path::Path;

    // ── All four boundary modules must be detected ──

    #[test]
    fn boundary_io_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/io/writer.rs"
        )));
    }

    #[test]
    fn boundary_ffi_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/ffi/bindings.rs"
        )));
    }

    #[test]
    fn boundary_runtime_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/runtime/executor.rs"
        )));
    }

    #[test]
    fn boundary_dir_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/boundary/adapter.rs"
        )));
    }

    // ── Non-boundary modules must NOT be detected ──

    #[test]
    fn non_boundary_module_is_not_detected() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/pipeline/reducer.rs"
        )));
    }

    #[test]
    fn non_boundary_domain_module_is_not_detected() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/config/settings.rs"
        )));
    }

    // ── File-level boundary module (.rs file matching a marker name) ──

    #[test]
    fn file_level_io_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new("src/io.rs")));
    }

    #[test]
    fn file_level_runtime_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/runtime.rs"
        )));
    }

    // ── Substring boundary markers must NOT match ──

    #[test]
    fn iostream_is_not_a_boundary_module() {
        // "iostream" contains "io" as a substring but the path component
        // is "iostream", not "io", so it must not match.
        assert!(!path_contains_boundary_component(Path::new(
            "src/iostream/reader.rs"
        )));
    }

    // ── UI tests ──

    #[test]
    fn ui() {
        dylint_testing::ui_test(env!("CARGO_PKG_NAME"), "ui");
    }
}
