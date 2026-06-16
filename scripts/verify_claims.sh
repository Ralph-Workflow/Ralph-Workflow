#!/usr/bin/env bash
# verify_claims.sh — pre-deploy gate: blocks deployment if unverified claims exist
# Run: bash scripts/verify_claims.sh (exits 0=clean, 1=violations found)
set -euo pipefail

WORKSPACE="/home/mistlight/.openclaw/workspace"
RALPH_SITE="$WORKSPACE/Ralph-Site"
RALPH_WF="$WORKSPACE/ralph-workflow/ralph-workflow"
LEDGER="$WORKSPACE/CLAIMS_LEDGER.md"
VIOLATIONS=0

red()   { echo -e "\033[31m$1\033[0m"; }
green() { echo -e "\033[32m$1\033[0m"; }
yellow(){ echo -e "\033[33m$1\033[0m"; }

echo "=== Claim Verification Gate ==="
echo ""

# --- Rule 1: No dead links (asciinema recordings, 404 repos we know about) ---
echo -n "[1] Dead link scan... "
DEAD=$(grep -r 'asciinema\|JDnY0' "$RALPH_SITE/app/views" "$RALPH_SITE/content" "$RALPH_WF/README.md" 2>/dev/null | grep -v '.git/' | grep -v 'node_modules/' || true)
if [ -n "$DEAD" ]; then
    red "FAIL"
    echo "$DEAD"
    VIOLATIONS=$((VIOLATIONS + 1))
else
    green "PASS"
fi

# --- Rule 2: Numbers near stats-words must have a source citation ---
echo -n "[2] Numeric claims sourcing... "
UNSOURCED=$(grep -rPn '(?<!href=")(?<!\>)\b\d{2,}\s+(tests|PRs|merged PRs|modules|LOC|specialized agents|quality gates|valid transitions|providers|sprint)|\d{2,3}\s+HN points|\d+,\d{3}\+\s+(installs|downloads|stars)|\d+\s+agents\b|\d{2,}\s+(state|transition|quality gate)' \
    "$RALPH_SITE/app/views/pages/compare.html.erb" \
    "$RALPH_SITE/content/blog/" \
    2>/dev/null | grep -v '.git/' || true)
FILTERED=""
while IFS= read -r line; do
    [ -z "$line" ] && continue
    CONTENT=$(echo "$line" | cut -d: -f3-)
    if echo "$CONTENT" | grep -qP '\([a-z]+\.[a-z]+.*20\d{2}\)'; then continue; fi
    if echo "$CONTENT" | grep -qP 'href="https?://(news\.ycombinator\.com|github\.com)/'; then continue; fi
    if echo "$CONTENT" | grep -qP 'pepy\.tech'; then continue; fi
    FILTERED="${FILTERED}${line}"$'\n'
done <<< "$UNSOURCED"
FILTERED=$(echo "$FILTERED" | grep -v '^$' || true)
if [ -n "$FILTERED" ]; then
    red "FAIL — unsourced numeric claims:"
    echo "$FILTERED" | head -20
    VIOLATIONS=$((VIOLATIONS + 1))
else
    green "PASS"
fi

# --- Rule 3: No fabrication patterns from the 2026-06-16 audit ---
echo -n "[3] Fabrication pattern scan... "
FABS=""
for PATTERN in \
    'acquired by|Acquired by' \
    'credits Ralph Workflow as (its|the) (inspiration|predecessor)' \
    'by (Orbit|Recusive|Mizerness|ottiwr)' \
    'cleanest admission yet' \
    'strongest (independent validation|signal) yet' \
    'none of the (five|several) variants' \
    'collectively have fewer HN points'; do
    HITS=$(grep -rPn "$PATTERN" "$RALPH_SITE/app/views" "$RALPH_SITE/content" 2>/dev/null | grep -v '.git/' | grep -v 'node_modules/' || true)
    if [ -n "$HITS" ]; then
        while IFS= read -r aline; do
            [ -z "$aline" ] && continue
            if ! grep -qF "$aline" "$LEDGER" 2>/dev/null; then
                FABS="${FABS}${aline}"$'\n'
            fi
        done <<< "$HITS"
    fi
done
FABS=$(echo "$FABS" | grep -v '^$' || true)
if [ -n "$FABS" ]; then
    red "FAIL"
    echo "$FABS" | head -10
    VIOLATIONS=$((VIOLATIONS + 1))
else
    green "PASS"
fi

# --- Rule 4: SHOWCASE.md must separate pattern ecosystem from product credits ---
echo -n "[4] SHOWCASE.md conflation scan... "
SHOWCASE="$WORKSPACE/SHOWCASE.md"
if ! grep -q 'Pattern Ecosystem\|NOT Ralph Workflow credits\|0 verified' "$SHOWCASE" 2>/dev/null; then
    yellow "WARN — SHOWCASE.md does not clearly separate pattern from product credits"
    echo "  Add 'Pattern Ecosystem' heading or explicit NOT-RW-credit disclaimers."
else
    green "PASS"
fi

# --- Rule 5: No stale statistics (>30 days without re-verification) ---
echo -n "[5] Stale stat scan... "
STALE=$(grep -n '169,000' "$RALPH_SITE/app/views/pages/compare.html.erb" 2>/dev/null || true)
if [ -n "$STALE" ]; then
    yellow "WARN — stale Bernstein install count (169,000+ vs actual ~320,000)"
else
    green "PASS"
fi

# --- Rule 6: Blog fabrication patterns ---
echo -n "[6] Blog fabrication scan... "
FAB_BLOG=""
if grep -rq 'miserness\|Mizerness' "$RALPH_SITE/content/blog/" 2>/dev/null; then
    FAB_BLOG="${FAB_BLOG}"$'\n'"  miserness/ralphy referenced but repo does NOT exist on GitHub"
fi
if grep -rq 'Orbit/Recusive' "$RALPH_SITE/content/blog/" 2>/dev/null; then
    FAB_BLOG="${FAB_BLOG}"$'\n'"  Orbit/Recusive referenced but org does NOT exist on GitHub"
fi
if grep -rq 'ottiwroteit\|ottiwr' "$RALPH_SITE/content/blog/" 2>/dev/null; then
    FAB_BLOG="${FAB_BLOG}"$'\n'"  ottiwroteit referenced — correct owner is ikamensh"
fi
FAB_BLOG=$(echo "$FAB_BLOG" | grep -v '^$' || true)
if [ -n "$FAB_BLOG" ]; then
    red "FAIL"
    echo "$FAB_BLOG"
    VIOLATIONS=$((VIOLATIONS + 1))
else
    green "PASS"
fi

# --- Rule 7: YC batch claims ---
echo -n "[7] YC batch claim scan... "
YC_UNVER=$(grep -rn 'YC P26\|YC S25\|YC W26' \
    "$RALPH_SITE/app/views" \
    "$RALPH_SITE/content/blog/" 2>/dev/null | grep -v '.git/' || true)
if [ -n "$YC_UNVER" ]; then
    yellow "WARN — unverified YC batch claims found:"
    echo "$YC_UNVER" | head -10
else
    green "PASS"
fi

# --- Rule 8: Linked repos must actually exist (spot-check 5 per run) ---
echo -n "[8] GitHub repo verification (spot-check 5)... "
BAD_REPOS=""
REPO_LINKS=$(grep -roPh 'github\.com/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+' \
    "$RALPH_SITE/app/views/pages/compare.html.erb" \
    "$RALPH_SITE/content/blog/" 2>/dev/null | \
    grep -v 'Ralph-Workflow/Ralph-Workflow\|ralph-workflow/ralph-workflow\|RalphWorkflow\|Ralph-Site\|freestyle-sh' | \
    sort -u || true)
CHECKED=0
while IFS= read -r repo; do
    [ -z "$repo" ] && continue
    [ "$CHECKED" -ge 5 ] && break
    OWNER_REPO=$(echo "$repo" | sed 's|github\.com/||')
    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "https://api.github.com/repos/$OWNER_REPO" 2>/dev/null)
    if [ "$HTTP_CODE" = "404" ]; then
        BAD_REPOS="${BAD_REPOS}$repo (404)"$'\n'
    fi
    CHECKED=$((CHECKED + 1))
done <<< "$REPO_LINKS"
BAD_REPOS=$(echo "$BAD_REPOS" | grep -v '^$' || true)
if [ -n "$BAD_REPOS" ]; then
    red "FAIL — dead GitHub repos linked:"
    echo "$BAD_REPOS"
    VIOLATIONS=$((VIOLATIONS + 1))
else
    green "PASS (spot-checked $CHECKED repos)"
fi

echo ""
echo "=== Result: $VIOLATIONS violation(s) ==="
if [ "$VIOLATIONS" -gt 0 ]; then
    red "DEPLOY BLOCKED — fix violations above before deploying."
    exit 1
else
    green "DEPLOY CLEARED — all claim gates passed."
    exit 0
fi
