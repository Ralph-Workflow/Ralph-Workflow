use rustc_hir::intravisit::FnKind;
use rustc_hir::{Body, FnDecl};
use rustc_lint::{LateContext, LateLintPass, LintContext};
use rustc_session::{declare_lint, declare_lint_pass};

declare_lint! {
    pub FORBID_RAW_EFFECT_TYPES_IN_PUBLIC_APIS,
    Warn,
    "raw effect-native types (e.g., std::process::Output, std::process::Child) must not leak across public API boundaries"
}

declare_lint_pass!(ForbidRawEffectTypesInPublicApis => [FORBID_RAW_EFFECT_TYPES_IN_PUBLIC_APIS]);

const FORBIDDEN_TYPES: &[&str] = &["std::process::Output", "std::process::Child"];

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[FORBID_RAW_EFFECT_TYPES_IN_PUBLIC_APIS]);
    lint_store.register_late_pass(|_| Box::new(ForbidRawEffectTypesInPublicApis));
}

impl<'tcx> LateLintPass<'tcx> for ForbidRawEffectTypesInPublicApis {
    fn check_fn(
        &mut self,
        cx: &LateContext<'tcx>,
        kind: FnKind<'tcx>,
        _decl: &'tcx FnDecl<'tcx>,
        _body: &'tcx Body<'tcx>,
        span: rustc_span::Span,
        def_id: rustc_hir::def_id::LocalDefId,
    ) {
        if matches!(kind, FnKind::Closure) {
            return;
        }

        if !cx.tcx.visibility(def_id.to_def_id()).is_public() {
            return;
        }

        let func_ty = cx.tcx.type_of(def_id.to_def_id());
        let ty_str = func_ty.skip_binder().to_string();

        if FORBIDDEN_TYPES.iter().any(|t| ty_str.contains(t)) {
            cx.span_lint(FORBID_RAW_EFFECT_TYPES_IN_PUBLIC_APIS, span, |diag| {
                diag.primary_message(format!(
                    "public function returns `{}`, a raw effect-native type",
                    FORBIDDEN_TYPES
                        .iter()
                        .find(|t| ty_str.contains(*t))
                        .unwrap()
                ));
                diag.help(
                    "boundary functions should translate raw effect types into domain types. \
                     See `docs/code-style/boundaries.md`.",
                );
            });
        }
    }
}
