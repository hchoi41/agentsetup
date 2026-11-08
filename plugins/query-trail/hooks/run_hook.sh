#!/usr/bin/env bash
# run_hook.sh: fail-closed fallback for the query-trail hooks (design spec v2 section 1.5; fix brief r1 B-M7).
#
# hooks.json runs every hook as
#   PYTHONUTF8=1 python -I -X utf8 -B "${CLAUDE_PLUGIN_ROOT}/hooks/<hook>.py" || "${BASH:-bash}" run_hook.sh $? <hook>.py
# so this script runs only when `python` did not exit 0. Arguments: the exit code, the hook file name.
#   - exit 2 from the hook (a block) passes through unchanged;
#   - otherwise the hook payload is read from stdin. If stdin is empty, `python` started and consumed it
#     (then failed): a PreToolUse hook blocks (exit 2), because a policy check that could not run must not
#     let the call through; Stop and PostToolUse hooks report and exit 0 (they are not a boundary);
#   - if the payload is still there (`python` missing, or a stub such as the Microsoft Store alias that
#     never read it), QT_PY, python3 and `py -3` are tried in that order (Python 3.10 or later);
#   - with no working interpreter: exit 2 with a message; a Stop hook still honours stop_hook_active.
#
# Builtins only (read, case, command -v), so it works with an empty PATH.
# Limitation: hook commands need bash (Git Bash on Windows). Under cmd.exe the `PYTHONUTF8=1 python ...`
# form is not valid syntax and the hook fails open (Claude Code treats a non-2 exit as non-blocking);
# Claude Code on Windows runs hook commands through Git Bash, which is what this plugin assumes.
code="${1:-1}"
hook="${2:-}"
dir="${BASH_SOURCE[0]%/*}"
case "$code" in
  0|2) exit "$code" ;;
esac
payload=""
IFS= read -r -d '' payload || true
case "$hook" in
  stop_check.py|csv_check.py) blocking=0 ;;
  *) blocking=1 ;;
esac
if [ -z "$payload" ]; then
  echo "query-trail hook: BLOCKED: $hook did not run cleanly under python (exit $code); fix the interpreter (set QT_PY or put Python 3.10+ first on PATH) and retry." >&2
  if [ "$blocking" = 1 ]; then exit 2; fi
  exit 0
fi
try() {
  "$@" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" </dev/null >/dev/null 2>&1 || return 1
  PYTHONUTF8=1 "$@" -I -X utf8 -B "$dir/$hook" <<<"$payload"
  exit $?
}
if [ -n "${QT_PY:-}" ]; then try "$QT_PY"; fi
if command -v python3 >/dev/null 2>&1; then try python3; fi
if command -v py >/dev/null 2>&1; then try py -3; fi
case "$hook" in
  stop_check.py)
    case "$payload" in
      *'"stop_hook_active": true'*|*'"stop_hook_active":true'*) exit 0 ;;
    esac ;;
esac
echo "query-trail hook: BLOCKED: no Python interpreter could run $hook (tried python, QT_PY, python3, py -3; exit $code). Install Python 3.10+, set QT_PY, or turn off the Microsoft Store python alias." >&2
exit 2
