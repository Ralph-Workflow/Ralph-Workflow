use crate::domain::boundary::is_in_boundary_module;
use rustc_hir::intravisit::{walk_expr, Visitor};
use rustc_hir::{Expr, ExprKind, QPath};
use rustc_lint::{LateContext, LateLintPass, LintContext};
use rustc_session::{declare_lint, declare_lint_pass};

declare_lint! {
    pub FORBID_BOUNDARY_RETRY_LOOPS,
    Deny,
    "retry-policy loops inside boundary modules must use a retry policy helper"
}

declare_lint_pass!(ForbidBoundaryRetryLoops => [FORBID_BOUNDARY_RETRY_LOOPS]);

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[FORBID_BOUNDARY_RETRY_LOOPS]);
    lint_store.register_late_pass(|_| Box::new(ForbidBoundaryRetryLoops));
}

const EFFECT_PATTERNS: &[(&[&str], &str)] = &[
    (&["std", "fs"], "filesystem operation"),
    (&["std", "env"], "environment access"),
    (&["std", "process"], "process operation"),
    (&["std", "net"], "network operation"),
    (&["reqwest"], "network operation"),
    (&["ureq"], "network operation"),
    (&["std", "thread"], "thread operation"),
    (&["tokio", "task"], "task operation"),
    (&["tokio", "time"], "time operation"),
    (&["rand"], "randomness operation"),
    (&["getrandom"], "randomness operation"),
];

const RETRY_COUNTER_NAMES: &[&str] = &[
    "attempt",
    "attempts",
    "retry",
    "retries",
    "retry_count",
    "retry_attempt",
];

fn path_matches_effect(def_path: &str) -> Option<&'static str> {
    EFFECT_PATTERNS
        .iter()
        .find(|(pattern, _)| {
            let prefix = pattern.join("::");
            def_path.starts_with(&prefix)
        })
        .map(|(_, desc)| *desc)
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

fn is_effect_call(cx: &LateContext<'_>, expr: &Expr<'_>) -> bool {
    if let Some(def_path) = get_call_def_path(cx, expr) {
        if path_matches_effect(&def_path).is_some() {
            return true;
        }
    }
    if let Some(def_path) = get_method_def_path(cx, expr) {
        if path_matches_effect(&def_path).is_some() {
            return true;
        }
    }
    false
}

struct RetryLoopChecker<'a, 'tcx> {
    cx: &'a LateContext<'tcx>,
    has_effect: bool,
    has_counter_var: bool,
    has_increment: bool,
    has_max_check: bool,
}

impl<'a, 'tcx> Visitor<'tcx> for RetryLoopChecker<'a, 'tcx> {
    fn visit_expr(&mut self, expr: &'tcx Expr<'_>) {
        if is_effect_call(self.cx, expr) {
            self.has_effect = true;
        }

        match &expr.kind {
            ExprKind::AssignOp(_, target, _) => {
                if let ExprKind::Path(QPath::Resolved(_, path)) = &target.kind {
                    let name = path
                        .segments
                        .last()
                        .map(|s| s.ident.name.as_str())
                        .unwrap_or("");
                    if RETRY_COUNTER_NAMES.iter().any(|n| name.contains(n)) {
                        self.has_increment = true;
                    }
                }
            }
            ExprKind::Assign(target, value, _) => {
                if let ExprKind::Path(QPath::Resolved(_, path)) = &target.kind {
                    let name = path
                        .segments
                        .last()
                        .map(|s| s.ident.name.as_str())
                        .unwrap_or("");
                    if RETRY_COUNTER_NAMES.iter().any(|n| name.contains(n)) {
                        self.has_increment = true;
                        if let ExprKind::Binary(_, left, _) = &value.kind {
                            let left_str = format!("{:?}", left.kind);
                            if left_str.contains("retry")
                                || left_str.contains("attempt")
                                || left_str.contains("retry_count")
                            {
                                self.has_increment = true;
                            }
                        }
                    }
                }
            }
            ExprKind::Path(QPath::Resolved(_, path)) => {
                let name = path
                    .segments
                    .last()
                    .map(|s| s.ident.name.as_str())
                    .unwrap_or("");
                if RETRY_COUNTER_NAMES.iter().any(|n| name.contains(n)) {
                    self.has_counter_var = true;
                }
            }
            ExprKind::Binary(op, left, right) => {
                let op_str = format!("{:?}", op);
                if op_str.contains("Ge") || op_str.contains("Gt") || op_str.contains("Eq") {
                    let left_str = format!("{:?}", left.kind);
                    let right_str = format!("{:?}", right.kind);
                    if (left_str.contains("retry")
                        || left_str.contains("attempt")
                        || left_str.contains("retry_count"))
                        && (right_str.contains("max")
                            || right_str.contains("Max")
                            || right_str.parse::<u32>().is_ok())
                    {
                        self.has_max_check = true;
                    }
                }
            }
            _ => {}
        }

        walk_expr(self, expr);
    }
}

fn check_loop_for_retry<'a>(cx: &LateContext<'a>, loop_expr: &'a Expr<'a>) -> bool {
    let ExprKind::Loop(block, _, _, _) = &loop_expr.kind else {
        return false;
    };

    let mut checker = RetryLoopChecker {
        cx,
        has_effect: false,
        has_counter_var: false,
        has_increment: false,
        has_max_check: false,
    };

    for stmt in block.stmts {
        checker.visit_stmt(stmt);
    }

    if let Some(expr) = block.expr {
        checker.visit_expr(expr);
    }

    checker.has_effect && checker.has_counter_var && checker.has_increment && checker.has_max_check
}

impl<'tcx> LateLintPass<'tcx> for ForbidBoundaryRetryLoops {
    fn check_expr(&mut self, cx: &LateContext<'tcx>, expr: &'tcx Expr<'tcx>) {
        if !is_in_boundary_module(cx, expr.span) {
            return;
        }

        if matches!(expr.kind, ExprKind::Loop(..)) {
            if check_loop_for_retry(cx, expr) {
                cx.span_lint(FORBID_BOUNDARY_RETRY_LOOPS, expr.span, |diag| {
                    diag.primary_message(
                        "retry loop with effect call and counter is forbidden in boundary modules",
                    );
                    diag.help(
                        "retry-policy loops must use a dedicated retry policy helper. \
                         Inline retry loops that both perform I/O and track attempts \
                         obscure error handling policy. Extract to a boundary helper.",
                    );
                });
            }
        }
    }
}
