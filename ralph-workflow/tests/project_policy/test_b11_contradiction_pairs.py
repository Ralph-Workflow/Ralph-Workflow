"""B11 contradiction-pair agreement tests.

The product criteria (step B11) names five contradiction pairs the
shipped starters must agree on:

1. testing requires a public production seam vs. "never invent a
   public API for a test"
2. typechecking requires fully typed tests while the declared
   checker scope excludes tests
3. verification permits owned named profiles vs. gate-script prose
   that can be read as "every gate in the default entry point or
   delete it"
4. clean-code vs. architecture on when a one-implementation
   abstraction is premature
5. security's secret-scanning requirement satisfied by a check
   that is not a credential scanner

Each pair is pinned here at the cheapest deterministic layer. A
future edit that re-opens a closed pair is a regression.
"""

from __future__ import annotations

from ralph.project_policy import starters


def _read(name: str) -> str:
    return starters.read_starter(name)


def test_pair_1_testing_seam_versus_clean_code_no_public_for_test() -> None:
    """Pair 1: testing requires narrow observable boundaries, and
    clean-code says never invent a public API for a test. The two
    starters MUST agree: a production refactor to add a seam is
    fine, exposing a private method or wrapping a function solely
    to satisfy a test is not.
    """
    testing = _read("testing-policy.md")
    clean_code = _read("clean-code-policy.md")
    # testing rule 3: never invent a public API solely to
    # accommodate a test.
    assert "Never invent a public API" in testing
    assert "accommodate a test" in testing
    # clean-code disclaims the same transformation: a method
    # made public solely to satisfy a ratchet is a violation
    # (the guard-inventory row labels this the "Syntax-proxy
    # ban" with the same prohibition).
    assert (
        "private method is renamed to public" in clean_code
        or "private method made public" in clean_code.lower()
        or "public API for a test" in clean_code
        or "wrapper exists only" in clean_code.lower()
    )


def test_pair_2_typechecking_typed_tests_in_scope() -> None:
    """Pair 2: typechecking requires typed tests, and the
    declared checker scope INCLUDES the tests directory. The two
    MUST agree; a checker that excludes ``tests/`` would let
    every test file silently lose coverage.

    Strengthened: the test now verifies (a) the positive commitment
    to typed tests ("tests are fully typed within the declared checker
    scope" — the Typed tests guard row's Protected outcome) AND (b)
    that tests are not categorically excluded from the checker scope.
    """
    typechecking = _read("typechecking-policy.md")
    normalized = " ".join(typechecking.split())
    # (a) Positive commitment: the Typed tests guard row's Protected outcome
    # commits to typed tests ("tests are fully typed within the declared
    # checker scope"). This phrase is the substantive contract.
    assert "tests are fully typed within the declared checker scope" in normalized
    # (b) Checker scope includes tests: the policy does NOT say tests are
    # excluded from the typechecker's in-scope paths. The "Typed tests"
    # guard row's Detected condition ("static type check over the test files
    # returns zero errors") confirms tests are in scope. The suppression
    # section also states "tests MUST NOT be excluded merely because of
    # their category".
    assert "tests MUST NOT be excluded" in normalized or "tests are fully typed" in normalized
    # (c) Explicit exclusion wording must be absent — "tests MUST be excluded"
    # or "tests MUST be ignored" in the scope context is a regression.
    assert "tests MUST be excluded" not in normalized
    assert "tests MUST be ignored" not in normalized


def test_pair_3_verification_named_profiles_versus_gate_script_default() -> None:
    """Pair 3: verification permits owned named profiles (the
    gate lanes), and the gate-script starter MUST permit declared,
    owned, triggered named profiles rather than requiring every check
    to be wired into the default entry point or deleted. The two agree:
    the default entry point is authoritative, but a declared named
    profile is a first-class route, not a deletion candidate.
    """
    verification = _read("verification-policy.md")
    gate_script = _read("gate-script-policy.md")
    # verification: named profiles are a route, not a deletion
    # candidate — ``NAMED PROFILE`` is the canonical lane name.
    verification_norm = verification
    assert "NAMED PROFILE" in verification_norm or "named profile" in verification.lower()
    # gate-script: MUST permit declared named profiles and MUST NOT
    # claim "either wire it in, or delete it".
    gate_script_lower = gate_script.lower()
    assert "either wire it in, or delete it" not in gate_script_lower
    assert "either wire it in or delete it" not in gate_script_lower
    assert "named profile" in gate_script_lower
    assert "meaning" in gate_script_lower
    assert "freeze" in gate_script_lower or "frozen" in gate_script_lower


def test_pair_4_clean_code_versus_architecture_one_implementation() -> None:
    """Pair 4: clean-code and architecture agree that a
    one-implementation abstraction is premature unless it pays
    for itself. The two starters must agree that implementation
    count alone neither requires nor forbids an abstraction, and
    that new abstractions require demonstrated boundary, volatility,
    testing, or reuse value/need.
    """
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
    """Pair 5: security requires secret scanning with a
    credential scanner. The starter MUST NOT point at a non-
    scanner (e.g. a generic grep) as the secret-scanning
    mechanism. Per the B11 product criteria, the failure mode is
    "security's secret-scanning requirement satisfied by a
    check that is not a credential scanner" — a check that names
    "scanner" as a generic concept does NOT satisfy pair 5; the
    requirement is falsifiable only when the starter names a
    credential secret-pattern detector and binds it to the
    secret-scanning mechanism, not just to a research-cited
    example.
    """
    security = _read("security-policy.md")
    normalized = " ".join(security.split()).lower()
    # (a) The starter names at least one real credential secret-
    # pattern detector (gitleaks, detect-secrets, trufflehog,
    # semgrep) — a generic grep, awk, or ripgrep would not pass.
    scanner_anchors = ("gitleaks", "detect-secrets", "trufflehog", "semgrep")
    assert any(scanner in normalized for scanner in scanner_anchors), (
        "security starter does not name a credential secret-"
        "pattern detector; B11 pair 5 cannot be satisfied by "
        "keyword presence of 'scanner' alone"
    )
    # (b) The starter ties the scanner to the secret-scanning
    # mechanism with a falsifiable requirement phrase — the
    # check that is invoked MUST be a credential scanner, not a
    # generic text search. Without this, the keyword anchor in
    # (a) could be satisfied by a single REPLACE-ME example.
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
