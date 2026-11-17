#!/usr/bin/env bash
set -euo pipefail

PINNED_HARNESS_COMMAND="timeout 720 claude -p --print --output-format=stream-json --model claude-opus-5-5 --permission-mode auto"
PINNED_MODEL_ID="claude-opus-5-5"
RUN_TIMEOUT_SECONDS=720
LEDGER_RELATIVE_PATH="tmp/policy-v6-replay-runs/ledger.jsonl"
SCORES_RELATIVE_PATH="tmp/policy-v6-replay-runs/scores.json"
MIN_FREE_KB=524288
MIN_FREE_INODES=100000
IDENTIFIER_PATTERN='^[A-Za-z0-9._-]+$'
MODEL_MISMATCH_EXIT=3
INVALID_RUN_EXIT=4
MISSING_INPUT_EXIT=5
TIMEOUT_EXIT=6
CRASH_EXIT=7
SCORING_FAILURE_EXIT=8

usage() {
  printf 'usage: %s run <run_id> <arm_id> <category> <snapshot_dir> <prompt_file> [scoring_prompt_file]\n' "$0"
}

json_escape() {
  local text="$1"
  text="${text//\\/\\\\}"
  text="${text//\"/\\\"}"
  printf '%s' "${text}"
}

repo_relative_path() {
  local path="$1"
  if [[ "${path}" == "${repo_root}/"* ]]; then
    printf '%s' "${path#"${repo_root}/"}"
  else
    printf '%s' "${path}"
  fi
}

append_ledger_row() {
  local outcome="$1"
  local wall_seconds="$2"
  local invalid_text="$3"
  local scores_file="$4"
  local invalid_json="null"
  local scores_json="null"
  if [[ -n "${invalid_text}" ]]; then
    invalid_json="\"$(json_escape "${invalid_text}")\""
  fi
  if [[ -n "${scores_file}" ]]; then
    scores_json="\"$(json_escape "${scores_file}")\""
  fi
  printf '{"run_id":"%s","arm":"%s","category":"%s","outcome":"%s","wall_seconds":%s,"invalid":%s,"scores_path":%s}\n' \
    "${run_id}" "${arm_id}" "${category}" "${outcome}" "${wall_seconds}" "${invalid_json}" "${scores_json}" \
    >> "${ledger_path}"
}

write_capture_meta() {
  local agent_exit_json="$1"
  local wall_seconds="$2"
  local invalid_text="$3"
  local invalid_json="null"
  if [[ -n "${invalid_text}" ]]; then
    invalid_json="\"$(json_escape "${invalid_text}")\""
  fi
  printf '{"run_id":"%s","arm":"%s","category":"%s","model":"%s","agent_exit":%s,"wall_seconds":%s,"invalid":%s}\n' \
    "${run_id}" "${arm_id}" "${category}" "${PINNED_MODEL_ID}" "${agent_exit_json}" "${wall_seconds}" \
    "${invalid_json}" \
    > "${capture_dir}/meta.json"
}

run_cold_scoring_pass() {
  local scoring_prompt_file="$1"
  local scoring_text
  local scoring_prompt
  local scoring_exit
  scoring_text="$(cat "${scoring_prompt_file}")"
  scoring_prompt="${scoring_text}
SCORING_PASS: score the capture of arm ${arm_id} in ${capture_dir} and write the arm scores to ${scores_path} in the scores.json schema."
  set +e
  (
    cd "${capture_dir}" || exit 70
    setsid -w ${PINNED_HARNESS_COMMAND} "${scoring_prompt}"
  ) > "${capture_dir}/scoring-transcript.jsonl" 2>&1
  scoring_exit=$?
  set -e
  if [[ "${scoring_exit}" -ne 0 ]]; then
    printf 'cold scoring pass failed with exit %s\n' "${scoring_exit}" >&2
    return 1
  fi
  if [[ ! -f "${scores_path}" ]]; then
    printf 'cold scoring pass wrote no scores file at %s\n' "${scores_path}" >&2
    return 1
  fi
  return 0
}

if [[ $# -lt 6 ]]; then
  usage >&2
  exit 2
fi

if [[ "$1" != "run" ]]; then
  usage >&2
  exit 2
fi

shift
run_id="$1"
arm_id="$2"
category="$3"
snapshot_dir="$4"
prompt_file="$5"
scoring_prompt_file="${6:-}"

for identifier in "${run_id}" "${arm_id}" "${category}"; do
  if [[ ! "${identifier}" =~ ${IDENTIFIER_PATTERN} ]]; then
    printf 'refusing to start: run_id, arm_id and category must match %s\n' "${IDENTIFIER_PATTERN}" >&2
    exit 2
  fi
done

if [[ -n "${RALPH_AGENT_MODEL:-}" && "${RALPH_AGENT_MODEL}" != "${PINNED_MODEL_ID}" ]]; then
  printf 'refusing to start: RALPH_AGENT_MODEL=%s differs from the pinned %s\n' \
    "${RALPH_AGENT_MODEL}" "${PINNED_MODEL_ID}" >&2
  exit "${MODEL_MISMATCH_EXIT}"
fi
export RALPH_AGENT_MODEL="${PINNED_MODEL_ID}"
export RALPH_V5_REPLAY_RUN_ID="${run_id}"

package_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$(cd "${package_root}/.." && pwd)"
ledger_path="${package_root}/${LEDGER_RELATIVE_PATH}"
capture_root="${package_root}/$(dirname "${LEDGER_RELATIVE_PATH}")"
capture_dir="${capture_root}/${run_id}"
scores_path="${package_root}/${SCORES_RELATIVE_PATH}"

mkdir -p "${capture_root}" "${capture_dir}"

if ! df -Pk "${capture_root}" | awk -v min="${MIN_FREE_KB}" 'NR==2 {exit !($4 >= min)}'; then
  append_ledger_row "invalid" "0" "ENOSPC: free disk blocks below ${MIN_FREE_KB} KiB" ""
  write_capture_meta "null" "0" "ENOSPC: free disk blocks below ${MIN_FREE_KB} KiB"
  printf 'ENOSPC precheck failed: %s has fewer than %s KiB free\n' "${capture_root}" "${MIN_FREE_KB}" >&2
  exit "${INVALID_RUN_EXIT}"
fi

if ! df -Pi "${capture_root}" | awk -v min="${MIN_FREE_INODES}" 'NR==2 {exit !($4 >= min)}'; then
  append_ledger_row "invalid" "0" "ENOSPC: free inodes below ${MIN_FREE_INODES}" ""
  write_capture_meta "null" "0" "ENOSPC: free inodes below ${MIN_FREE_INODES}"
  printf 'inode precheck failed: %s has fewer than %s free inodes\n' "${capture_root}" "${MIN_FREE_INODES}" >&2
  exit "${INVALID_RUN_EXIT}"
fi

if ! command -v claude > /dev/null 2>&1; then
  append_ledger_row "invalid" "0" "provider unavailable: claude is not on PATH" ""
  write_capture_meta "null" "0" "provider unavailable: claude is not on PATH"
  printf 'provider unavailable: claude is not on PATH\n' >&2
  exit "${INVALID_RUN_EXIT}"
fi

if [[ ! -d "${snapshot_dir}" ]]; then
  append_ledger_row "invalid" "0" "snapshot directory does not exist: ${snapshot_dir}" ""
  write_capture_meta "null" "0" "snapshot directory does not exist: ${snapshot_dir}"
  printf 'snapshot directory does not exist: %s\n' "${snapshot_dir}" >&2
  exit "${MISSING_INPUT_EXIT}"
fi

if [[ ! -f "${prompt_file}" ]]; then
  append_ledger_row "invalid" "0" "prompt file does not exist: ${prompt_file}" ""
  write_capture_meta "null" "0" "prompt file does not exist: ${prompt_file}"
  printf 'prompt file does not exist: %s\n' "${prompt_file}" >&2
  exit "${MISSING_INPUT_EXIT}"
fi

if [[ -n "${scoring_prompt_file}" && ! -f "${scoring_prompt_file}" ]]; then
  append_ledger_row "invalid" "0" "scoring prompt file does not exist: ${scoring_prompt_file}" ""
  write_capture_meta "null" "0" "scoring prompt file does not exist: ${scoring_prompt_file}"
  printf 'scoring prompt file does not exist: %s\n' "${scoring_prompt_file}" >&2
  exit "${MISSING_INPUT_EXIT}"
fi

prompt_text="$(cat "${prompt_file}")"
printf '%s\n' "${prompt_text}" > "${capture_dir}/prompt.md"

start_ts="$(date +%s)"
set +e
(
  cd "${snapshot_dir}" || exit 70
  setsid -w ${PINNED_HARNESS_COMMAND} "${prompt_text}"
) > "${capture_dir}/transcript.jsonl" 2>&1
agent_exit=$?
set -e
end_ts="$(date +%s)"
wall_seconds=$(( end_ts - start_ts ))

printf '%s\n' "${agent_exit}" > "${capture_dir}/agent_exit"
printf '%s\n' "${wall_seconds}" > "${capture_dir}/wall_seconds"

if [[ "${agent_exit}" -eq 124 ]]; then
  append_ledger_row "failed" "${wall_seconds}" "" ""
  write_capture_meta "${agent_exit}" "${wall_seconds}" ""
  printf 'run %s (%s) timed out after %s s; recorded as a section 6 failure on the pack\n' \
    "${run_id}" "${arm_id}" "${RUN_TIMEOUT_SECONDS}" >&2
  exit "${TIMEOUT_EXIT}"
fi

if [[ "${agent_exit}" -ne 0 ]]; then
  append_ledger_row "failed" "${wall_seconds}" "" ""
  write_capture_meta "${agent_exit}" "${wall_seconds}" ""
  printf 'run %s (%s) crashed with exit %s; recorded as a section 6 failure on the pack\n' \
    "${run_id}" "${arm_id}" "${agent_exit}" >&2
  exit "${CRASH_EXIT}"
fi

if [[ -f "${snapshot_dir}/final.md" ]]; then
  cp "${snapshot_dir}/final.md" "${capture_dir}/final.md"
fi

if git -C "${snapshot_dir}" rev-parse --is-inside-work-tree > /dev/null 2>&1; then
  git -C "${snapshot_dir}" diff > "${capture_dir}/diff.patch" || true
fi

scores_recorded=""
if [[ -n "${scoring_prompt_file}" ]]; then
  if ! run_cold_scoring_pass "${scoring_prompt_file}"; then
    append_ledger_row "invalid" "${wall_seconds}" "cold scoring pass failed" ""
    write_capture_meta "${agent_exit}" "${wall_seconds}" "cold scoring pass failed"
    exit "${SCORING_FAILURE_EXIT}"
  fi
  scores_recorded="$(repo_relative_path "${scores_path}")"
fi

append_ledger_row "valid" "${wall_seconds}" "" "${scores_recorded}"
write_capture_meta "${agent_exit}" "${wall_seconds}" ""

printf 'run %s (%s) completed in %s s\n' "${run_id}" "${arm_id}" "${wall_seconds}"
