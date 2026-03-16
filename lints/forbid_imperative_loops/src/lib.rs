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

use rustc_ast::ast::{Expr, ExprKind};
use rustc_lint::{EarlyContext, EarlyLintPass, LintContext};
use rustc_span::FileName;
use std::path::{Component, Path};

/// Boundary module path components where imperative loops are permitted.
const BOUNDARY_MODULES: &[&str] = &["io", "runtime", "ffi", "boundary"];

dylint_linting::impl_early_lint! {
    /// ### What it does
    ///
    /// Rejects `while`, `loop`, and `for` loop constructs outside of
    /// explicitly whitelisted boundary modules.
    ///
    /// ### FP principle: avoid explicit recursion and imperative iteration
    ///
    /// In functional programming, data transformations are expressed as
    /// compositions of higher-order functions (`map`, `filter`, `fold`,
    /// `flat_map`, etc.) rather than step-by-step mutation inside a loop
    /// body.  The Haskell community summarises this as "avoid explicit
    /// recursion" — prefer combinators that make the *shape* of the
    /// transformation visible in the type (HaskellWiki: "Avoid explicit
    /// recursion").
    ///
    /// Imperative loops in Rust encourage mutable accumulators, index
    /// manipulation, and `break`/`continue` control flow that obscures
    /// intent.  Iterator pipelines express the same work declaratively
    /// and compose better.
    ///
    /// ### Boundary exceptions
    ///
    /// Boundary code (I/O polling, retry loops, byte-level parsing)
    /// sometimes genuinely needs an imperative loop because the
    /// surrounding API is inherently effectful.  Place such code in a
    /// module whose path contains a boundary marker (`io/`, `runtime/`,
    /// `ffi/`, `boundary/`).  This mirrors the Haskell separation
    /// between pure computation and the `IO` monad.
    ///
    /// ### Example (bad — imperative accumulation)
    ///
    /// ```rust,ignore
    /// let mut result = Vec::new();
    /// for item in items {
    ///     if item.is_valid() {
    ///         result.push(item.transform());
    ///     }
    /// }
    /// ```
    ///
    /// ### Example (good — declarative pipeline)
    ///
    /// ```rust,ignore
    /// let result: Vec<_> = items
    ///     .into_iter()
    ///     .filter(|item| item.is_valid())
    ///     .map(|item| item.transform())
    ///     .collect();
    /// ```
    pub FORBID_IMPERATIVE_LOOPS,
    Warn,
    "imperative loops (`while`, `loop`, `for`) are forbidden outside boundary modules",
    ForbidImperativeLoops
}

#[derive(Default)]
pub struct ForbidImperativeLoops;

fn path_contains_boundary_component(path: &Path) -> bool {
    path.components().any(|component| match component {
        Component::Normal(name) => {
            let name_str = name.to_str().unwrap_or("");
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

impl EarlyLintPass for ForbidImperativeLoops {
    fn check_expr(&mut self, cx: &EarlyContext<'_>, expr: &Expr) {
        let loop_kind = match &expr.kind {
            ExprKind::While(_, _, _) => Some("while"),
            ExprKind::Loop(_, _, _) => Some("loop"),
            ExprKind::ForLoop { .. } => Some("for"),
            _ => None,
        };

        let Some(kind_name) = loop_kind else {
            return;
        };

        if is_in_boundary_module(cx, expr.span) {
            return;
        }

        cx.span_lint(FORBID_IMPERATIVE_LOOPS, expr.span, |diag| {
            diag.primary_message(format!(
                "`{kind_name}` loop is forbidden outside boundary modules"
            ));
            diag.help(
                "use iterator combinators (map, filter, fold, for_each, etc.) \
                 instead of imperative loops",
            );
            diag.note(
                "if a loop is genuinely required for I/O or runtime logic, move this code \
                 into a boundary module (io/, runtime/, ffi/, boundary/)",
            );
        });
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
            "src/io/poller.rs"
        )));
    }

    #[test]
    fn boundary_runtime_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/runtime/executor.rs"
        )));
    }

    #[test]
    fn boundary_ffi_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/ffi/bindings.rs"
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
            "src/pipeline/state.rs"
        )));
    }

    #[test]
    fn non_boundary_domain_module_is_not_detected() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/reducer/logic.rs"
        )));
    }

    // ── File-level boundary module (.rs file matching a marker name) ──

    #[test]
    fn file_level_io_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new("src/io.rs")));
    }

    // ── Substring boundary markers must NOT match ──

    #[test]
    fn iostream_is_not_a_boundary_module() {
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
