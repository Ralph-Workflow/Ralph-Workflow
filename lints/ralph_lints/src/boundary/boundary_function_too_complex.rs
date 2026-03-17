//! Lint: `BOUNDARY_FUNCTION_TOO_COMPLEX`
//!
//! Warns when a boundary function grows large enough to likely mix wiring with
//! business policy. Boundary code may perform effects, but it should still stay
//! thin: gather inputs, call pure helpers, execute effects, translate the
//! result, and return.

use crate::domain::boundary::{
    boundary_function_complexity_score, boundary_function_needs_split,
    collect_boundary_function_metrics, is_in_boundary_module,
};
use rustc_hir::intravisit::FnKind;
use rustc_hir::{Body, FnDecl};
use rustc_lint::{LateContext, LateLintPass, LintContext};
use rustc_session::{declare_lint, declare_lint_pass};
use rustc_span::Span;

declare_lint! {
    /// ### What it does
    ///
    /// Warns when a boundary function is both large and branch-heavy.
    ///
    /// ### Why is this bad?
    ///
    /// `boundary/` code should be a thin composition seam. If one boundary
    /// function grows long and contains multiple decisions, it usually means
    /// business policy is being embedded inline instead of delegated to pure
    /// helpers.
    pub BOUNDARY_FUNCTION_TOO_COMPLEX,
    Warn,
    "boundary functions should stay thin and avoid embedding policy"
}

declare_lint_pass!(BoundaryFunctionTooComplex => [BOUNDARY_FUNCTION_TOO_COMPLEX]);

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[BOUNDARY_FUNCTION_TOO_COMPLEX]);
    lint_store.register_late_pass(|_| Box::new(BoundaryFunctionTooComplex));
}

impl<'tcx> LateLintPass<'tcx> for BoundaryFunctionTooComplex {
    fn check_fn(
        &mut self,
        cx: &LateContext<'tcx>,
        kind: FnKind<'tcx>,
        _decl: &'tcx FnDecl<'tcx>,
        body: &'tcx Body<'tcx>,
        span: Span,
        def_id: rustc_hir::def_id::LocalDefId,
    ) {
        if !is_in_boundary_module(cx, span) {
            return;
        }

        if matches!(kind, FnKind::Closure) {
            return;
        }

        let Some(metrics) = collect_boundary_function_metrics(cx, body, span) else {
            return;
        };

        if !boundary_function_needs_split(&metrics) {
            return;
        }

        let complexity_score = boundary_function_complexity_score(&metrics);
        let name = cx.tcx.item_name(def_id.to_def_id()).as_str().to_string();

        cx.span_lint(BOUNDARY_FUNCTION_TOO_COMPLEX, span, |diag| {
            diag.primary_message(format!(
                "boundary function `{name}` appears to mix wiring with policy"
            ));
            diag.help(
                "split this boundary into thin wiring plus pure policy helpers: keep the boundary to gathering inputs, calling pure functions, executing effects, translating the result, and returning. See `docs/code-style/boundaries.md`.",
            );
            diag.note(format!(
                "this function spans {} lines, has {} top-level statements, {} decision points, {} boolean guard operators, {} match arms, max nesting depth {}, and complexity score {}; move branching policy into pure domain helpers. Style guide: `docs/code-style/boundaries.md` and `docs/code-style/architecture.md`.",
                metrics.line_count,
                metrics.statement_count,
                metrics.decision_points,
                metrics.boolean_operators,
                metrics.match_arms,
                metrics.max_nesting_depth,
                complexity_score,
            ));
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::metrics::count_decision_points;

    fn metrics(
        line_count: usize,
        statement_count: usize,
        decision_points: usize,
        boolean_operators: usize,
        match_arms: usize,
        max_nesting_depth: usize,
    ) -> crate::domain::boundary::BoundaryFunctionMetrics {
        crate::domain::boundary::BoundaryFunctionMetrics {
            line_count,
            statement_count,
            decision_points,
            boolean_operators,
            match_arms,
            max_nesting_depth,
        }
    }

    #[test]
    fn short_boundary_function_is_allowed() {
        assert!(!boundary_function_needs_split(&metrics(10, 3, 2, 0, 2, 1)));
    }

    #[test]
    fn large_boundary_function_with_multiple_decisions_warns() {
        assert!(boundary_function_needs_split(&metrics(16, 7, 2, 1, 3, 2)));
    }

    #[test]
    fn large_linear_boundary_function_does_not_warn() {
        assert!(!boundary_function_needs_split(&metrics(22, 10, 0, 0, 0, 1)));
    }

    #[test]
    fn counts_loops_as_decision_points() {
        let source = "for item in items { work(item); }\nwhile ready { keep_going(); }\n";

        assert_eq!(count_decision_points(source), 2);
    }

    #[test]
    fn nested_branching_warns_before_function_gets_very_long() {
        assert!(boundary_function_needs_split(&metrics(13, 8, 3, 2, 3, 3)));
    }
}
