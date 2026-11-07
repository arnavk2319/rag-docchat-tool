#!/usr/bin/env bash
# PostToolUse (Edit|Write|MultiEdit): run kit.json "format_file" and "lint_file" on the changed file.
# Commands get the relative path appended. Output trimmed to 50 lines. Exit 2 returns errors to the agent.
set -u
in=$(cat)
fp=$(jq -r '.tool_input.file_path // empty' <<<"$in")
[ -z "$fp" ] && exit 0
root=${CLAUDE_PROJECT_DIR:-$(pwd)}
cd "$root" || exit 0
rel=${fp#"$root"/}
[ -f "$rel" ] || exit 0
case "$rel" in node_modules/* | .next/* | .venv/* | dist/* | *lock* | *.md | docs/*) exit 0 ;; esac
cfg=.claude/kit.json
[ -f "$cfg" ] || exit 0
fmt=$(jq -r '.format_file // empty' "$cfg")
lint=$(jq -r '.lint_file // empty' "$cfg")
exts=$(jq -r '(.code_extensions // ["ts","tsx","js","mjs","py"]) | join("|")' "$cfg")
grep -Eq "\.($exts)$" <<<"$rel" || exit 0

out=""
fail=0
if [ -n "$fmt" ]; then
  $fmt "$rel" >/dev/null 2>&1 || { out+=$($fmt "$rel" 2>&1); fail=1; }
fi
if [ -n "$lint" ]; then
  l=$($lint "$rel" 2>&1) || { out+=$'\n'"$l"; fail=1; }
fi
if [ "$fail" = 1 ]; then
  echo "$out" | tail -50 >&2
  exit 2
fi
exit 0
