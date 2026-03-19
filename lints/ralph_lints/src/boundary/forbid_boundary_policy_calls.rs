use crate::domain::boundary::is_in_boundary_module;
use rustc_hir::{Expr, ExprKind};
use rustc_lint::{LateContext, LateLintPass, LintContext};
use rustc_session::{declare_lint, declare_lint_pass};

declare_lint! {
    pub FORBID_BOUNDARY_POLICY_CALLS,
    Deny,
    "calls from boundary modules to reducer/orchestrator policy helpers are forbidden"
}

declare_lint_pass!(ForbidBoundaryPolicyCalls => [FORBID_BOUNDARY_POLICY_CALLS]);

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[FORBID_BOUNDARY_POLICY_CALLS]);
    lint_store.register_late_pass(|_| Box::new(ForbidBoundaryPolicyCalls));
}

fn get_call_def_path(cx: &LateContext<'_>, expr: &Expr<'_>) -> Option<String> {
    let ExprKind::Call(func, _) = &expr.kind else {
        return None;
    };
    let ExprKind::Path(qpath) = &func.kind else {
        return None;
    };
    let res = cx.typeck_results().qpath_res(qpath, func.hir_id);
    let rustc_hir::def::Res::Def(_, def_id) = res else {
        return None;
    };
    Some(cx.tcx.def_path_str(def_id))
}

fn get_method_def_path(cx: &LateContext<'_>, expr: &Expr<'_>) -> Option<String> {
    let ExprKind::MethodCall(..) = &expr.kind else {
        return None;
    };
    let def_id = cx.typeck_results().type_dependent_def_id(expr.hir_id)?;
    Some(cx.tcx.def_path_str(def_id))
}

fn is_policy_helper(def_path: &str) -> bool {
    let segments: Vec<_> = def_path.split("::").collect();
    for window in segments.windows(2) {
        if let [prev, next] = window {
            if (*prev == "reducer" || *prev == "orchestrator") && next.starts_with("determine_") {
                return true;
            }
            if (*prev == "reducer" || *prev == "orchestrator") && next.starts_with("reduce_") {
                return true;
            }
        }
    }
    false
}

impl<'tcx> LateLintPass<'tcx> for ForbidBoundaryPolicyCalls {
    fn check_expr(&mut self, cx: &LateContext<'tcx>, expr: &'tcx Expr<'tcx>) {
        if !is_in_boundary_module(cx, expr.span) {
            return;
        }

        if let Some(def_path) = get_call_def_path(cx, expr) {
            if is_policy_helper(&def_path) {
                cx.span_lint(FORBID_BOUNDARY_POLICY_CALLS, expr.span, |diag| {
                    diag.primary_message(format!(
                        "call to policy helper `{}` is forbidden in boundary modules",
                        def_path
                    ));
                    diag.help(
                        "boundary modules must not call reducer/orchestrator policy helpers directly. \
                         Policy decisions belong in domain code, not at the boundary.",
                    );
                });
                return;
            }
        }

        if let Some(def_path) = get_method_def_path(cx, expr) {
            if is_policy_helper(&def_path) {
                cx.span_lint(FORBID_BOUNDARY_POLICY_CALLS, expr.span, |diag| {
                    diag.primary_message(format!(
                        "call to policy helper `{}` is forbidden in boundary modules",
                        def_path
                    ));
                    diag.help(
                        "boundary modules must not call reducer/orchestrator policy helpers directly. \
                         Policy decisions belong in domain code, not at the boundary.",
                    );
                });
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::is_policy_helper;

    #[test]
    fn detects_reducer_determine_next_effect() {
        assert!(is_policy_helper("reducer::determine_next_effect"));
    }

    #[test]
    fn detects_reducer_reduce_review() {
        assert!(is_policy_helper("reducer::reduce_review"));
    }

    #[test]
    fn detects_orchestrator_determine_next_effect() {
        assert!(is_policy_helper("orchestrator::determine_next_effect"));
    }

    #[test]
    fn detects_orchestrator_reduce_event() {
        assert!(is_policy_helper("orchestrator::reduce_event"));
    }

    #[test]
    fn does_not_match_unrelated_function() {
        assert!(!is_policy_helper("std::fs::read_to_string"));
    }

    #[test]
    fn does_not_match_non_policy_reducer() {
        assert!(!is_policy_helper("reducer::some_helper"));
    }

    #[test]
    fn does_not_match_non_policy_orchestrator() {
        assert!(!is_policy_helper("orchestrator::process_result"));
    }

    #[test]
    fn does_not_match_different_naming_pattern() {
        assert!(!is_policy_helper("reducer::handle_event"));
        assert!(!is_policy_helper("orchestrator::execute_step"));
    }
}
