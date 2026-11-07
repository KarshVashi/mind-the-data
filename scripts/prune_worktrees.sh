#!/usr/bin/env bash
# Remove finished worktrees under .claude/worktrees/ and report the space freed.
#
#   scripts/prune_worktrees.sh            # remove
#   scripts/prune_worktrees.sh --dry-run  # only say what would go
#
# A worktree is removed only when all of these hold:
#   - it has no uncommitted changes and no untracked files (ignored files such
#     as .venv and .terraform go with it);
#   - it is not locked, not the worktree this script runs from, and nothing
#     in it (the directory, index or HEAD) changed in the last IDLE_HOURS
#     hours (default 24), so a session still working there is left alone;
#   - its work is safe elsewhere: HEAD is already in origin/main, or a merged
#     pull request's head is exactly HEAD (squash merges leave no ancestry,
#     so the PR is the proof that nothing is unpushed).
# Anything else is kept and the reason printed. Remote branches are never
# touched; the local branch of a removed worktree is deleted.

set -euo pipefail

idle_hours=${IDLE_HOURS:-24}
dry_run=false
case "${1:-}" in
  --dry-run | -n) dry_run=true ;;
  "") ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

common_dir=$(git rev-parse --path-format=absolute --git-common-dir)
repo_root=$(dirname "$common_dir")
worktrees_dir="$repo_root/.claude/worktrees"
here=$(git rev-parse --show-toplevel)

if [ ! -d "$worktrees_dir" ]; then
  echo "no $worktrees_dir; nothing to prune"
  exit 0
fi

git -C "$repo_root" fetch --quiet --prune origin
have_gh=false
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  have_gh=true
else
  echo "gh unavailable: only worktrees already in origin/main can be pruned"
fi

# Prints why the worktree must stay, or nothing when it can go.
keep_reason() {
  local path=$1 branch=$2 head=$3 locked=$4
  if [ "$path" = "$here" ]; then echo "this is the current worktree"; return; fi
  if [ "$locked" = true ]; then echo "locked"; return; fi
  # Fail closed: if git cannot read the worktree, keep it.
  local changes git_dir
  if ! changes=$(git -C "$path" status --porcelain 2>&1); then
    echo "git status failed: ${changes%%$'\n'*}"; return
  fi
  if [ -n "$changes" ]; then echo "uncommitted or untracked changes"; return; fi
  if ! git_dir=$(git -C "$path" rev-parse --absolute-git-dir 2>/dev/null); then
    echo "cannot find its git directory"; return
  fi
  if [ -n "$(find "$path" "$git_dir/index" "$git_dir/HEAD" "$git_dir/logs/HEAD" \
    -maxdepth 0 -mmin "-$((idle_hours * 60))" 2>/dev/null)" ]; then
    echo "used in the last ${idle_hours}h (IDLE_HOURS=0 to ignore)"; return
  fi
  if git -C "$repo_root" merge-base --is-ancestor "$head" origin/main 2>/dev/null; then
    return
  fi
  if [ -z "$branch" ]; then echo "detached HEAD not in origin/main"; return; fi
  if [ "$have_gh" = true ]; then
    local pr
    pr=$(gh pr list --state all --head "$branch" --json number,state,headRefOid \
      --jq ".[] | select(.headRefOid == \"$head\" and .state == \"MERGED\") | .number" \
      2>/dev/null | head -n 1)
    if [ -n "$pr" ]; then return; fi
    echo "no merged PR whose head is $head"
    return
  fi
  echo "not in origin/main, and gh is unavailable to check for a merged PR"
}

freed_kb=0
removed=0
kept=0
path="" head="" branch="" locked=false

consider() {
  [ -n "$path" ] || return 0
  case "$path" in "$worktrees_dir"/*) ;; *) return 0 ;; esac
  local name reason size_kb
  name=$(basename "$path")
  reason=$(keep_reason "$path" "$branch" "$head" "$locked")
  if [ -n "$reason" ]; then
    echo "keep   $name (${branch:-detached}): $reason"
    kept=$((kept + 1))
    return 0
  fi
  size_kb=$(du -sk "$path" 2>/dev/null | cut -f1)
  freed_kb=$((freed_kb + ${size_kb:-0}))
  removed=$((removed + 1))
  if [ "$dry_run" = true ]; then
    echo "would remove $name (${branch:-detached}), $((${size_kb:-0} / 1024)) MB"
    return 0
  fi
  echo "remove $name (${branch:-detached}), $((${size_kb:-0} / 1024)) MB"
  git -C "$repo_root" worktree remove "$path"
  if [ -n "$branch" ]; then
    git -C "$repo_root" branch -D "$branch" >/dev/null
  fi
}

while IFS= read -r line; do
  case "$line" in
    "worktree "*) path=${line#worktree } ;;
    "HEAD "*) head=${line#HEAD } ;;
    "branch "*) branch=${line#branch refs/heads/} ;;
    locked*) locked=true ;;
    "")
      consider
      path="" head="" branch="" locked=false
      ;;
  esac
done < <(git -C "$repo_root" worktree list --porcelain; echo)

if [ "$dry_run" = false ]; then
  git -C "$repo_root" worktree prune
fi
verb=$([ "$dry_run" = true ] && echo "would free" || echo "freed")
echo "$removed removed, $kept kept; $verb $((freed_kb / 1024)) MB"
