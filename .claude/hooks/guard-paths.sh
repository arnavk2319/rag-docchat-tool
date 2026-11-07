#!/usr/bin/env bash
# PreToolUse (Edit|Write|MultiEdit|NotebookEdit). Exit 2 blocks.
# Blocks .env* (except .env.example), secrets/, .github/ and .claude/. Extra protected prefixes come from
# .claude/kit.json "protected_paths". Human maintenance: launch Claude Code with KIT_ALLOW_PROTECTED=1.
set -u
in=$(cat)
fp=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' <<<"$in")
[ -z "$fp" ] && exit 0
[ "${KIT_ALLOW_PROTECTED:-}" = "1" ] && exit 0

root=${CLAUDE_PROJECT_DIR:-$(pwd)}
rel=${fp#"$root"/}
rel=${rel#./}
case "/$rel/" in
  */../*) echo "guard-paths: blocked path with '..' segments: $fp" >&2; exit 2 ;;
esac

base=$(basename "$rel")
case "$base" in
  .env.example) ;;
  .env | .env.*) echo "guard-paths: .env files are off limits to agents ($rel)" >&2; exit 2 ;;
esac

case "$rel" in
  secrets/* | */secrets/*) echo "guard-paths: secrets/ is off limits ($rel)" >&2; exit 2 ;;
  .github/*) echo "guard-paths: .github/ is human-only; describe the change in the ticket ($rel)" >&2; exit 2 ;;
  .claude/*) echo "guard-paths: .claude/ is human-only ($rel)" >&2; exit 2 ;;
esac

cfg="$root/.claude/kit.json"
if [ -f "$cfg" ]; then
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    case "$rel" in "$p"*) echo "guard-paths: $p is protected (kit.json protected_paths) ($rel)" >&2; exit 2 ;; esac
  done < <(jq -r '.protected_paths[]? // empty' "$cfg")
fi
exit 0
