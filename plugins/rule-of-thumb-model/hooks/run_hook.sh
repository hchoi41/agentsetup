#!/usr/bin/env bash
# run_hook.sh: fail-open fallback for the rule-of-thumb-model hooks (design section 13.1, plan F16 d).
#
# hooks.json runs every hook as
#   PYTHONUTF8=1 python -I -X utf8 -B "${CLAUDE_PLUGIN_ROOT}/hooks/<hook>.py" || "${BASH:-bash}" run_hook.sh $? <hook>.py
# so this script runs only when python did not exit 0. Arguments: the exit code, the hook file name.
#   - 1 (the hook caught its own failure and printed one line) and 2 (a PreToolUse refusal) pass through;
#   - any other code means Python could not start or could not load the hook library (the hooks exit 3 then).
#     If a model folder (a folder holding rotm_model.json) lies at or above the payload's cwd, the payload's
#     file_path or $PWD, one line names the hook and says the action was let through, and the exit code is 1
#     (Claude Code treats 1 as a non-blocking error and shows the line to the user). Outside a model folder it
#     exits 0 with no output, so a missing Python never disturbs work outside model folders.
# These hooks are not a security boundary (unlike query-trail's): a hook that cannot run never blocks.
# Builtins only (read, case, [[ ]]), so it works with an empty PATH.
code="${1:-1}"
hook="${2:-hook}"
name="${hook%.py}"
case "$code" in
  0|1|2) exit "$code" ;;
esac
payload=""
IFS= read -r -d '' payload 2>/dev/null || true
found=""
look_up() {
  local d="$1" i=0
  d="${d//\\\\//}"
  d="${d//\\//}"
  d="${d%/}"
  while [ -n "$d" ] && [ "$i" -le 40 ]; do
    if [ -f "$d/rotm_model.json" ]; then found="$d"; return 0; fi
    case "$d" in
      */*) d="${d%/*}" ;;
      *) return 1 ;;
    esac
    i=$((i + 1))
  done
  return 1
}
re_cwd='"cwd"[[:space:]]*:[[:space:]]*"([^"]*)"'
re_file='"(file_path|notebook_path)"[[:space:]]*:[[:space:]]*"([^"]*)"'
if [[ $payload =~ $re_cwd ]]; then look_up "${BASH_REMATCH[1]}"; fi
if [ -z "$found" ] && [[ $payload =~ $re_file ]]; then
  f="${BASH_REMATCH[2]}"
  f="${f//\\\\//}"
  look_up "${f%/*}"
fi
if [ -z "$found" ]; then look_up "$PWD"; fi
if [ -n "$found" ]; then
  echo "rule-of-thumb-model hook $name: could not run (python exit $code); the action was let through." >&2
  exit 1
fi
exit 0
