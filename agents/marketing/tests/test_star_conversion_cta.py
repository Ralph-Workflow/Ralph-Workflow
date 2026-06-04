"""Standalone tests for star_conversion_agent CTA extraction.
Run: python3 agents/marketing/tests/test_star_conversion_cta.py
"""
import sys
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
sys.path.insert(0, str(ROOT))

from agents.marketing.star_conversion_agent import _extract_cta_text

failures = 0
tests = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global failures, tests
    tests += 1
    if condition:
        print(f"  ✅ {name}")
    else:
        failures += 1
        print(f"  ❌ {name}{' — ' + detail if detail else ''}")


# Test 1: Real-world multi-line CODEBERG_STAR_CTA
onboarding = Path("/home/mistlight/Ralph-Workflow/ralph-workflow/ralph/onboarding.py")
text = onboarding.read_text()
result = _extract_cta_text(text)
check("multi-line real onboarding", len(result) > 20 and result != "(",
      f"got [{result[:80]}]")
check("'development priority' present", "development priority" in result,
      f"got [{result}]")
check("has star emoji", "⭐" in result, f"got [{result}]")

# Test 2: Three-line parenthesized
text2 = '''CODEBERG_STAR_CTA: Final[str] = (
    "Line one here "
    "line two continues "
    "line three ends it"
)'''
result2 = _extract_cta_text(text2)
check("three-line joined correctly",
      "Line one" in result2 and "line three" in result2,
      f"got [{result2}]")

# Test 3: Single line without type hint
text3 = '''CODEBERG_STAR_CTA = "⭐ Star the repo — it helps"'''
result3 = _extract_cta_text(text3)
check("single line without type hint",
      "Star the repo" in result3 and len(result3) > 10,
      f"got [{result3}]")

# Test 4: Single line with single quotes
text4 = "CODEBERG_STAR_CTA = '⭐ Star the repo'"
result4 = _extract_cta_text(text4)
check("single quotes", "Star the repo" in result4,
      f"got [{result4}]")

# Test 5: No CTA defined
result5 = _extract_cta_text("nothing here")
check("no CTA returns empty", result5 == "", f"got [{result5}]")

# Test 6: Whitespace collapsed
text6 = '''CODEBERG_STAR_CTA: Final[str] = (
    "hello     world "
    "  extra spaces   here  "
)'''
result6 = _extract_cta_text(text6)
check("whitespace collapsed", "hello world" in result6 and "extra spaces here" in result6,
      f"got [{result6}]")

# Test 7: CTA with only emoji
text7 = 'CODEBERG_STAR_CTA: Final[str] = "⭐"'
result7 = _extract_cta_text(text7)
check("emoji-only CTA", "⭐" in result7, f"got [{result7}]")

print(f"\n{tests - failures}/{tests} passed, {failures} failed")
sys.exit(0 if failures == 0 else 1)
