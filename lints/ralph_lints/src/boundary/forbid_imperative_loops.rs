//! Lint: `FORBID_IMPERATIVE_LOOPS`
//!
//! Rejects `while`, `loop`, and `for` loop constructs outside of boundary
//! modules.
//!
//! ## FP principle: avoid explicit recursion and imperative iteration
//!
//! In functional programming, data transformations are expressed as
//! compositions of higher-order functions (`map`, `filter`, `fold`, `flat_map`)
//! rather than step-by-step mutation inside a loop body.
//!
//! ## Boundary exceptions
//!
//! Boundary code (I/O polling, retry loops, byte-level parsing) sometimes
//! genuinely needs an imperative loop because the surrounding API is
//! inherently effectful.

use crate::domain::boundary::is_in_boundary_module;
use rustc_ast::ast::{Expr, ExprKind};
use rustc_lint::{EarlyContext, EarlyLintPass, LintContext};
use rustc_session::{declare_lint, declare_lint_pass};

declare_lint! {
    /// ### What it does
    ///
    /// Rejects `while`, `loop`, and `for` loop constructs outside of
    /// explicitly whitelisted boundary modules.
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
    "imperative loops (`while`, `loop`, `for`) are forbidden outside boundary modules"
}

declare_lint_pass!(ForbidImperativeLoops => [FORBID_IMPERATIVE_LOOPS]);

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[FORBID_IMPERATIVE_LOOPS]);
    lint_store.register_early_pass(|| Box::new(ForbidImperativeLoops));
}

const fn loop_kind(expr: &ExprKind) -> Option<&'static str> {
    match expr {
        ExprKind::While(_, _, _) => Some("while"),
        ExprKind::Loop(_, _, _) => Some("loop"),
        ExprKind::ForLoop { .. } => Some("for"),
        _ => None,
    }
}

impl EarlyLintPass for ForbidImperativeLoops {
    fn check_expr(&mut self, cx: &EarlyContext<'_>, expr: &Expr) {
        let Some(kind_name) = loop_kind(&expr.kind) else {
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
                "replace loop with iterator pipeline: `xs.filter(p).map(f).collect()`, \
                 `xs.fold(init, f)`, or `xs.any(p)` / `xs.all(p)`. \
                 See quick reference in `docs/code-style/functional-transformations.md`.",
            );
            diag.note(
                "if a loop is genuinely required for I/O or runtime logic, move this code \
                 into a boundary module (io/, runtime/, ffi/, boundary/). Style guides: \
                 `docs/code-style/functional-transformations.md` and \
                 `docs/code-style/boundaries.md`.",
            );
        });
    }
}
