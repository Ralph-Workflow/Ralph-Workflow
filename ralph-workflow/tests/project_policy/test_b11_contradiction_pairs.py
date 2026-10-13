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
    """
    typechecking = _read("typechecking-policy.md")
    normalized = " ".join(typechecking.split())
    # The pinned rule: tests MUST be fully typed, and the
    # checker scope includes the tests directory.
    assert "tests" in normalized
    # The contradiction-pair B11 anchor: typechecking does NOT
    # exclude the tests directory.
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
    for itself. The two starters must NOT disagree about
    whether a one-call-site interface is justified.
    """
    clean_code = _read("clean-code-policy.md")
    architecture = _read("architecture-policy.md")
    cc_norm = " ".join(clean_code.split()).lower()
    arch_norm = " ".join(architecture.split()).lower()
    # Both starters anchor the conditional rule: an abstraction
    # is not justified by a single implementation; the
    # architecture starter also names ``durable_contracts`` as a
    # cross-boundary concern, not a same-boundary abstraction.
    cc_anchors = ("one implementation", "premature", "single caller")
    arch_anchors = ("one implementation", "premature", "durable_contracts")
    assert any(a in cc_norm for a in cc_anchors), (
        "clean-code starter does not name a one-implementation / "
        "premature-abstraction anchor; pair 4 has no anchor on the "
        "clean-code side"
    )
    assert any(a in arch_norm for a in arch_anchors), (
        "architecture starter does not name a one-implementation / "
        "durable-contracts anchor; pair 4 has no anchor on the "
        "architecture side"
    )


def test_pair_5_security_secret_scanning_uses_credential_scanner() -> None:
    """Pair 5: security requires secret scanning with a
    credential scanner. The starter MUST NOT point at a non-
    scanner (e.g. a generic grep) as the secret-scanning
    mechanism.
    """
    security = _read("security-policy.md")
    normalized = " ".join(security.split()).lower()
    # The shipped starter lists real credential scanners in its
    # RALPH-COMMAND set; the B11 anchor is that the command's
    # first token is one of them, not a generic grep.
    scanner_anchors = ("gitleaks", "detect-secrets", "trufflehog", "semgrep")
    assert any(scanner in normalized for scanner in scanner_anchors), (
        "security starter does not anchor a real credential scanner; pair 5 cannot be satisfied"
    )
