from __future__ import annotations

from ralph.testing.audit_test_integrity import DELETED_TEST_MODULES, audit_test_integrity_diff
from tests.doc_roots import REPOSITORY_ROOT

_LIVE_TEST_MODULE = "ralph-workflow/tests/recovery/test_retry_prompt_contract.py"

_PRODUCTION_DIFF = """\
diff --git a/ralph/core.py b/ralph/core.py
index 1111111..2222222 100644
--- a/ralph/core.py
+++ b/ralph/core.py
@@ -1,3 +1,3 @@
 def run():
-    return 1
+    return 2
"""


def _test_diff(relative_path: str) -> str:
    return (
        f"diff --git a/{relative_path} b/{relative_path}\n"
        "index 3333333..4444444 100644\n"
        f"--- a/{relative_path}\n"
        f"+++ b/{relative_path}\n"
        "@@ -10,4 +10,4 @@\n"
        "     result = run()\n"
        "-    assert result == 1\n"
        "+    assert result == 2\n"
    )


def test_every_allowlisted_test_module_is_absent_from_the_tree() -> None:
    for relative_path, reason in DELETED_TEST_MODULES.items():
        assert not (REPOSITORY_ROOT / relative_path).exists(), (
            f"{relative_path} is allowlisted as a deleted test module but the file "
            "exists in the tree, so the allowlist suppresses findings for a live module"
        )
        assert reason.strip(), f"{relative_path} is allowlisted without a stated reason"


def test_a_test_module_that_exists_in_the_tree_is_not_allowlisted() -> None:
    assert (REPOSITORY_ROOT / _LIVE_TEST_MODULE).is_file()

    assert _LIVE_TEST_MODULE not in DELETED_TEST_MODULES


def test_a_weakened_assertion_in_a_live_test_module_is_reported() -> None:
    findings = audit_test_integrity_diff(_PRODUCTION_DIFF + _test_diff(_LIVE_TEST_MODULE))

    assert [finding.file_path for finding in findings] == [_LIVE_TEST_MODULE]


def test_a_weakened_assertion_in_an_allowlisted_deleted_module_is_not_reported() -> None:
    for relative_path in DELETED_TEST_MODULES:
        findings = audit_test_integrity_diff(_PRODUCTION_DIFF + _test_diff(relative_path))

        assert findings == [], relative_path
