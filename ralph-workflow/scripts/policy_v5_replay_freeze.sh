#!/usr/bin/env bash
set -euo pipefail

PINNED_V4_SHA="c67e7536fe8b97831c72d3e5d87644c26b4b2342"
PINNED_V5_SHA="f760933b2fae095ecdb8bf9f06ae8125a5dd9e90"
PINNED_RUN_ID_ROOT="tmp/policy-v5-replay-runs/<run_id>/"

package_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
canonical_plan="${package_root}/docs/agents/policy-v5-paired-replay-plan.md"
mirror_plan="${package_root}/docs/plans/policy-v5-paired-replay-plan.md"

usage() {
  printf 'usage: %s [--verify]\n' "$0"
}

verify_plan_copies() {
  if ! cmp -s "${canonical_plan}" "${mirror_plan}"; then
    printf 'plan copies differ: %s vs %s\n' "${canonical_plan}" "${mirror_plan}" >&2
    return 1
  fi
  local copy
  for copy in "${canonical_plan}" "${mirror_plan}"; do
    if ! grep -Fq "${PINNED_V4_SHA}" "${copy}"; then
      printf 'missing pinned v4 baseline sha in %s\n' "${copy}" >&2
      return 1
    fi
    if ! grep -Fq "${PINNED_V5_SHA}" "${copy}"; then
      printf 'missing pinned v5 treatment sha in %s\n' "${copy}" >&2
      return 1
    fi
    if ! grep -Fq "${PINNED_RUN_ID_ROOT}" "${copy}"; then
      printf 'missing pinned run id root in %s\n' "${copy}" >&2
      return 1
    fi
  done
  printf 'plan copies byte-identical; pinned run id and version metadata present\n'
}

case "${1:-freeze}" in
  freeze)
    cp "${canonical_plan}" "${mirror_plan}"
    verify_plan_copies
    ;;
  --verify)
    verify_plan_copies
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
