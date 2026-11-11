from __future__ import annotations

from ralph.project_policy import starters


def _read(name: str) -> str:
    return starters.read_starter(name)


def test_pair_1_testing_seam_versus_clean_code_no_public_for_test() -> None:
    testing = _read("testing-policy.md")
    clean_code = _read("clean-code-policy.md")
    assert "Never invent a public API" in testing
    assert "accommodate a test" in testing
    assert (
        "private method is renamed to public" in clean_code
        or "private method made public" in clean_code.lower()
        or "public API for a test" in clean_code
        or "wrapper exists only" in clean_code.lower()
    )


def test_pair_2_typechecking_typed_tests_in_scope() -> None:
    typechecking = _read("typechecking-policy.md")
    normalized = " ".join(typechecking.split())
    assert "tests are fully typed within the declared checker scope" in normalized
    assert "tests MUST NOT be excluded" in normalized or "tests are fully typed" in normalized
    assert "tests MUST be excluded" not in normalized
    assert "tests MUST be ignored" not in normalized


def test_pair_3_verification_named_profiles_versus_gate_script_default() -> None:
    verification = _read("verification-policy.md")
    gate_script = _read("gate-script-policy.md")
    verification_norm = verification
    assert "NAMED PROFILE" in verification_norm or "named profile" in verification.lower()
    gate_script_lower = gate_script.lower()
    assert "either wire it in, or delete it" not in gate_script_lower
    assert "either wire it in or delete it" not in gate_script_lower
    assert "named profile" in gate_script_lower
    assert "meaning" in gate_script_lower
    assert "freeze" in gate_script_lower or "frozen" in gate_script_lower


def test_pair_4_clean_code_versus_architecture_one_implementation() -> None:
    clean_code = _read("clean-code-policy.md")
    architecture = _read("architecture-policy.md")
    cc_norm = " ".join(clean_code.split()).lower()
    arch_norm = " ".join(architecture.split()).lower()

    phrase = "implementation count alone neither requires nor forbids"
    assert phrase in cc_norm, (
        f"clean-code starter does not state {phrase!r}; pair 4 agreement missing on clean-code side"
    )
    assert phrase in arch_norm, (
        f"architecture starter does not state {phrase!r}; "
        "pair 4 agreement missing on architecture side"
    )

    for surface_norm, starter_name in ((cc_norm, "clean-code"), (arch_norm, "architecture")):
        assert "boundary, volatility, testing, or reuse" in surface_norm, (
            f"{starter_name} starter missing demonstrated boundary, volatility, "
            "testing, or reuse requirement"
        )


def test_pair_5_security_secret_scanning_uses_credential_scanner() -> None:
    security = _read("security-policy.md")
    normalized = " ".join(security.split()).lower()
    scanner_anchors = ("gitleaks", "detect-secrets", "trufflehog", "semgrep")
    assert any(scanner in normalized for scanner in scanner_anchors), (
        "security starter does not name a credential secret-"
        "pattern detector; B11 pair 5 cannot be satisfied by "
        "keyword presence of 'scanner' alone"
    )
    falsifiable_requirement_anchors = (
        "real credential",
        "credential / vulnerability scanner",
        "credential/vulnerability scanner",
        "credential scanner",
        "secret scanner",
        "scanner is runnable",
    )
    assert any(phrase in normalized for phrase in falsifiable_requirement_anchors), (
        "security starter prose does not require a credential "
        "scanner as the secret-scanning mechanism; the "
        "requirement is keyword-anchored, not falsifiable — "
        "B11 pair 5"
    )
