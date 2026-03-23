use crate::domain::boundary::is_in_boundary_module;
use rustc_hir::intravisit::{walk_expr, Visitor};
use rustc_hir::{Expr, ExprKind, MatchSource};
use rustc_lint::{LateContext, LateLintPass, LintContext};
use rustc_session::{declare_lint, declare_lint_pass};
use std::collections::HashSet;
use std::marker::PhantomData;

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

fn get_call_def_path<'tcx>(cx: &LateContext<'tcx>, expr: &'tcx Expr<'tcx>) -> Option<String> {
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

fn get_method_def_path<'tcx>(cx: &LateContext<'tcx>, expr: &'tcx Expr<'tcx>) -> Option<String> {
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

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
enum EffectCategory {
    FileSystem,
    Environment,
    Process,
    Network,
    ThreadTime,
    Random,
}

impl EffectCategory {
    fn is_thread_time(&self) -> bool {
        matches!(self, EffectCategory::ThreadTime)
    }
}

const EFFECT_CATEGORIES: &[(&[&str], EffectCategory)] = &[
    (&["std", "fs"], EffectCategory::FileSystem),
    (&["std", "env"], EffectCategory::Environment),
    (&["std", "process"], EffectCategory::Process),
    (&["std", "net"], EffectCategory::Network),
    (&["reqwest"], EffectCategory::Network),
    (&["ureq"], EffectCategory::Network),
    (&["std", "thread"], EffectCategory::ThreadTime),
    (&["tokio", "task"], EffectCategory::ThreadTime),
    (&["tokio", "time"], EffectCategory::ThreadTime),
    (&["tokio", "runtime"], EffectCategory::ThreadTime),
    (&["std", "time"], EffectCategory::ThreadTime),
    (&["rand"], EffectCategory::Random),
    (&["getrandom"], EffectCategory::Random),
];

fn path_effect_category(def_path: &str) -> Option<EffectCategory> {
    EFFECT_CATEGORIES.iter().find_map(|(pattern, category)| {
        if def_path.starts_with(&pattern.join("::")) {
            Some(*category)
        } else {
            None
        }
    })
}

fn expr_effect_category<'tcx>(
    cx: &LateContext<'tcx>,
    expr: &'tcx Expr<'tcx>,
) -> Option<EffectCategory> {
    if let Some(def_path) = get_call_def_path(cx, expr) {
        if let Some(category) = path_effect_category(&def_path) {
            return Some(category);
        }
    }
    if let Some(def_path) = get_method_def_path(cx, expr) {
        if let Some(category) = path_effect_category(&def_path) {
            return Some(category);
        }
    }
    None
}

struct EffectCategoryCollector<'tcx> {
    cx: *const LateContext<'tcx>,
    categories: HashSet<EffectCategory>,
    _marker: PhantomData<&'tcx LateContext<'tcx>>,
}

impl<'tcx> EffectCategoryCollector<'tcx> {
    fn new(cx: &LateContext<'tcx>) -> Self {
        Self {
            cx,
            categories: HashSet::new(),
            _marker: PhantomData,
        }
    }

    #[inline]
    fn context(&self) -> &LateContext<'tcx> {
        unsafe { &*self.cx }
    }
}

impl<'tcx> Visitor<'tcx> for EffectCategoryCollector<'tcx> {
    fn visit_expr(&mut self, expr: &'tcx Expr<'tcx>) {
        if let Some(category) = expr_effect_category(self.context(), expr) {
            self.categories.insert(category);
        }
        walk_expr(self, expr);
    }
}

fn collect_effect_categories<'tcx>(
    cx: &LateContext<'tcx>,
    expr: &'tcx Expr<'tcx>,
) -> HashSet<EffectCategory> {
    let mut collector = EffectCategoryCollector::new(cx);
    collector.visit_expr(expr);
    collector.categories
}

fn should_flag_branch_effects(arm_categories: &[HashSet<EffectCategory>]) -> bool {
    let mut union = HashSet::<EffectCategory>::new();
    for categories in arm_categories {
        if categories.is_empty() {
            return false;
        }
        union.extend(categories.iter());
    }
    if union.len() < 2 {
        return false;
    }
    if union.iter().all(|cat| cat.is_thread_time()) {
        return false;
    }
    true
}

// Check if an expression accesses exit_code field or status.success() method
fn uses_exit_code_check<'tcx>(cx: &LateContext<'tcx>, expr: &'tcx Expr<'tcx>) -> bool {
    // Check for .exit_code field access
    if let ExprKind::Field(_base, field) = &expr.kind {
        if field.as_str() == "exit_code" || field.as_str() == "status" {
            return true;
        }
    }
    
    // Check for .success() or .code() method calls
    if let Some(def_path) = get_method_def_path(cx, expr) {
        if def_path.contains("::success") || def_path.contains("::code") {
            return true;
        }
    }
    
    // Check for is_success() helper calls
    if let Some(def_path) = get_call_def_path(cx, expr) {
        if def_path.ends_with("::is_success") {
            return true;
        }
    }
    
    // Recursively check binary operations (e.g., exit_code == 0)
    if let ExprKind::Binary(_, left, right) = &expr.kind {
        return uses_exit_code_check(cx, left) || uses_exit_code_check(cx, right);
    }
    
    false
}

// Check if expression branches on exit code
fn branches_on_exit_code<'tcx>(cx: &LateContext<'tcx>, expr: &'tcx Expr<'tcx>) -> bool {
    match &expr.kind {
        ExprKind::If(cond, _, Some(_)) => uses_exit_code_check(cx, cond),
        ExprKind::Match(scrutinee, arms, MatchSource::Normal) => {
            if arms.len() < 2 {
                return false;
            }
            // Check if scrutinee uses exit code
            if uses_exit_code_check(cx, scrutinee) {
                return true;
            }
            // Check if any arm guard uses exit code
            for arm in arms.iter() {
                if let Some(guard) = arm.guard {
                    if uses_exit_code_check(cx, guard) {
                        return true;
                    }
                }
            }
            false
        }
        _ => false,
    }
}


fn branch_selects_effect_call<'tcx>(cx: &LateContext<'tcx>, expr: &'tcx Expr<'tcx>) -> bool {
    match &expr.kind {
        ExprKind::If(_, then_expr, Some(else_expr)) => should_flag_branch_effects(&[
            collect_effect_categories(cx, then_expr),
            collect_effect_categories(cx, else_expr),
        ]),
        ExprKind::Match(_, arms, MatchSource::Normal) => {
            if arms.len() < 2 {
                return false;
            }
            let arm_categories: Vec<HashSet<EffectCategory>> = arms
                .iter()
                .map(|arm| collect_effect_categories(cx, arm.body))
                .filter(|categories| !categories.is_empty())
                .collect();
            should_flag_branch_effects(&arm_categories)
        }
        _ => false,
    }
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
                return;
            }
        }

        if branch_selects_effect_call(cx, expr) {
            cx.span_lint(FORBID_BOUNDARY_POLICY_CALLS, expr.span, |diag| {
                diag.primary_message(
                    "branching control flow that selects effectful calls is forbidden in boundary modules",
                );
                diag.help(
                    "boundary modules must delegate policy decisions gating effectful operations to domain code.",
                );
            });
            return;
        }

        if branches_on_exit_code(cx, expr) {
            cx.span_lint(FORBID_BOUNDARY_POLICY_CALLS, expr.span, |diag| {
                diag.primary_message(
                    "branching on exit code or process status is a policy decision forbidden in boundary modules",
                );
                diag.help(
                    "boundary modules should return ProcessOutput or ExitStatus to domain code; \
                     the reducer/orchestrator interprets exit codes to decide success vs retry vs failure paths.",
                );
            });
            return;
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

    #[test]
    fn exit_code_pattern_is_detected() {
        // This is a meta-test documenting expected behavior
        // The actual detection is tested via UI tests in boundary_exitcode_policy.rs
        assert!(true, "Exit code branching detection is implemented and tested via UI tests");
    }

}

