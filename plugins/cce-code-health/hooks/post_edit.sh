#!/bin/sh
# PostToolUse hook: per-file code-health feedback after Edit/Write/MultiEdit.
# Exit 0 silently unless `codehealth hook` finds a debt rise (exit 2, report on stderr).
input=$(cat)
command -v python3 >/dev/null 2>&1 || exit 0
file=$(printf '%s' "$input" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin)["tool_input"]["file_path"])
except Exception:
    pass' 2>/dev/null)
[ -n "$file" ] || exit 0
dir=$(dirname "$file")
[ -d "$dir" ] || exit 0
root=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || exit 0
if [ -n "$CODEHEALTH_BIN" ]; then
    bin=$CODEHEALTH_BIN
elif [ -x "$root/.venv-codehealth/bin/codehealth" ]; then
    bin=$root/.venv-codehealth/bin/codehealth
elif command -v codehealth >/dev/null 2>&1; then
    bin=codehealth
else
    exit 0
fi
printf '%s' "$input" | "$bin" hook
exit $?
