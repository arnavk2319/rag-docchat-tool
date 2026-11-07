#!/usr/bin/env bash
# PostToolUse (any): append tool name, path and exit code to the tool trace. Never file contents.
set -u
in=$(cat)
root=${CLAUDE_PROJECT_DIR:-$(pwd)}
sid=$(jq -r '.session_id // "unknown"' <<<"$in")
dir="$root/logs/runs/$(date -u +%F)"
mkdir -p "$dir"
jq -c --arg ts "$(date -u +%FT%TZ)" '{
  ts: $ts,
  tool: .tool_name,
  path: (.tool_input.file_path // .tool_input.notebook_path // .tool_input.path // null),
  exit: (.tool_response.exit_code // .tool_response.exitCode // null)
}' <<<"$in" >>"$dir/$sid.jsonl" 2>/dev/null
find "$root/logs/runs" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -r {} + 2>/dev/null
exit 0
