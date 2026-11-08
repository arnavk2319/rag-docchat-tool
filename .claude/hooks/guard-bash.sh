#!/usr/bin/env bash
# PreToolUse (Bash). Exit 2 on push to main, force push, production deploys, rm -rf,
# destructive DB commands, and schema push/migrate against a non-local database.
set -u
in=$(cat)
cmd=$(jq -r '.tool_input.command // empty' <<<"$in")
[ -z "$cmd" ] && exit 0
root=${CLAUDE_PROJECT_DIR:-$(pwd)}

block() { echo "guard-bash: blocked: $1" >&2; exit 2; }
has() { grep -Eiq -- "$1" <<<"$cmd"; }

if has '(^|[;&|[:space:]])git([[:space:]]+-C[[:space:]]+[^[:space:]]+)*[[:space:]]+push\b'; then
  has '(--force|--force-with-lease|[[:space:]]-f([[:space:]]|$)|[[:space:]]\+[^[:space:]]+)' && block "force push"
  has 'git([[:space:]]+-C[[:space:]]+[^[:space:]]+)*[[:space:]]+push[^;&|]*[[:space:]:/](main|master)([[:space:]]|$)' && block "push to main"
  # Branch of the checkout the push runs in: the hook cwd, then a leading `cd <dir> &&`, then `git -C <dir>`.
  cwd=$(jq -r '.cwd // empty' <<<"$in")
  dir=${cwd:-$root}
  cd_to=$(sed -nE 's/^[[:space:]]*cd[[:space:]]+([^;&|[:space:]]+)[[:space:]]*(&&|;).*/\1/p' <<<"$cmd" | head -1)
  c_to=$(sed -nE 's/.*git[[:space:]]+-C[[:space:]]+([^[:space:]]+).*/\1/p' <<<"$cmd" | head -1)
  branch=${KIT_BRANCH:-$(cd "$dir" 2>/dev/null && cd "${cd_to:-.}" 2>/dev/null && git -C "${c_to:-.}" branch --show-current 2>/dev/null)}
  case "$branch" in main | master) block "push while on $branch" ;; esac
fi

# Agents only enable auto-merge on PRs into the integration branch; they never merge directly or target prod.
if has '(^|[;&|[:space:]])gh[[:space:]]+pr[[:space:]]+merge\b'; then
  has '--auto' || block "gh pr merge without --auto (agents only enable auto-merge)"
fi
has '(^|[;&|[:space:]])gh[[:space:]]+pr[[:space:]]+create\b[^;&|]*--base[[:space:]=]+(main|master)\b' && block "PR into main (human opens integration -> main)"

has 'vercel[^;&|]*--prod\b' && block "vercel --prod (agents never promote)"
has 'vercel[^;&|]*[[:space:]]promote\b' && block "vercel promote"
has '(^|[;&|[:space:]])rm[[:space:]]+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r|-r[[:space:]]+-f|-f[[:space:]]+-r|--recursive[[:space:]]+--force)' && block "rm -rf"
has '\b(drop[[:space:]]+(table|database|schema)|truncate[[:space:]]+(table[[:space:]]+)?[a-z_"]+|dropdb)\b' && block "destructive DB command"

has 'prisma[[:space:]]+migrate[[:space:]]+reset' && block "prisma migrate reset"
if has '(drizzle-kit[[:space:]]+push|prisma[[:space:]]+(db[[:space:]]+push|migrate[[:space:]]+deploy)|alembic[[:space:]]+upgrade)'; then
  has '(localhost|127\.0\.0\.1|\[::1\])' || grep -Eq '(localhost|127\.0\.0\.1)' <<<"${DATABASE_URL:-}" ||
    block "schema push/migrate against a non-local database"
fi
exit 0
