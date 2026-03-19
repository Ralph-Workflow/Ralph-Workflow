use crate::domain::boundary::is_in_boundary_module;
use rustc_hir::{Expr, ExprKind, MatchSource, Pat, PatKind, StmtKind};
use rustc_lint::{LateContext, LateLintPass, LintContext};
use rustc_middle::ty::TyKind;
use rustc_session::{declare_lint, declare_lint_pass};

declare_lint! {
    pub FORBID_RESULT_SWALLOWING,
    Deny,
    "Result values must not be silently discarded"
}

declare_lint_pass!(ForbidResultSwallowing => [FORBID_RESULT_SWALLOWING]);

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[FORBID_RESULT_SWALLOWING]);
    lint_store.register_late_pass(|_| Box::new(ForbidResultSwallowing));
}

fn is_result_type(cx: &LateContext<'_>, expr: &Expr<'_>) -> bool {
    let ty = cx.typeck_results().expr_ty(expr);
    matches!(ty.kind(), TyKind::Adt(_, _) if ty.to_string().starts_with("Result<"))
}

impl<'tcx> LateLintPass<'tcx> for ForbidResultSwallowing {
    fn check_stmt(&mut self, cx: &LateContext<'tcx>, stmt: &'tcx rustc_hir::Stmt<'tcx>) {
        if let StmtKind::Let(let_stmt) = &stmt.kind {
            if let PatKind::Wild = let_stmt.pat.kind {
                if let Some(init) = let_stmt.init {
                    if is_result_type(cx, init) {
                        cx.span_lint(FORBID_RESULT_SWALLOWING, stmt.span, |diag| {
                            diag.primary_message("`let _ =` discards Result value");
                            diag.help(
                                "handle the Result explicitly with `match`, `if let Err`, or `?`",
                            );
                        });
                    }
                }
            }
        }
    }

    fn check_expr(&mut self, cx: &LateContext<'tcx>, expr: &'tcx Expr<'tcx>) {
        if is_in_boundary_module(cx, expr.span) {
            return;
        }

        match &expr.kind {
            ExprKind::MethodCall(method_call, receiver, _, _) => {
                if method_call.ident.as_str() == "ok" && is_result_type(cx, receiver) {
                    cx.span_lint(FORBID_RESULT_SWALLOWING, expr.span, |diag| {
                        diag.primary_message("`.ok()` discards Result value");
                        diag.help(
                            "handle the Result explicitly with `match`, `if let Err`, or `?`",
                        );
                    });
                }
            }
            ExprKind::Match(match_expr, arms, source) => {
                if !matches!(source, MatchSource::Normal) {
                    return;
                }
                if arms.len() != 1 {
                    return;
                }
                let arm = &arms[0];

                if !is_err_tuple_struct_pat(cx, &arm.pat) {
                    return;
                }

                if is_unit_expr(&arm.body) && is_result_type(cx, match_expr) {
                    cx.span_lint(FORBID_RESULT_SWALLOWING, expr.span, |diag| {
                        diag.primary_message("`if let Err(_)` discards Result value");
                        diag.help("handle the Result explicitly with `match` or `?`");
                    });
                }
            }
            _ => {}
        }
    }
}

fn is_err_tuple_struct_pat(cx: &LateContext<'_>, pat: &Pat<'_>) -> bool {
    if let PatKind::TupleStruct(qpath, inner, _) = &pat.kind {
        if inner.len() == 1 && matches!(inner[0].kind, PatKind::Wild) {
            let res = cx.typeck_results().qpath_res(qpath, pat.hir_id);
            if let rustc_hir::def::Res::Def(_, def_id) = res {
                return cx.tcx.def_path_str(def_id).ends_with("Err");
            }
        }
    }
    false
}

fn is_unit_expr(expr: &Expr<'_>) -> bool {
    matches!(expr.kind, ExprKind::Block(block, _) if block.stmts.is_empty() && block.expr.is_none())
}
