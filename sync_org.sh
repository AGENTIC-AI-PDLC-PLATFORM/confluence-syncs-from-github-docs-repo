#!/usr/bin/env bash
# Sync the docs of every repository of a GitHub organization into Confluence
# (one page tree per repository, page history = git history), via repo_to_confluence.py.
#
# Usage:  ./sync_org.sh <github-org> [topic] [-- extra repo_to_confluence.py args]
#   topic  only repositories carrying this GitHub topic (default: kb-sync; "" = every
#          non-archived repository). Opt a repo in with: gh repo edit org/repo --add-topic kb-sync
#
# Environment:
#   GH_TOKEN                read access to the org's repositories (GitHub App token or PAT)
#   CONFLUENCE_SPACE_KEY    target space, e.g. KB
#   ATLASSIAN_CLOUD_ID      cloudId or site URL, e.g. https://acme.atlassian.net
#   ATLASSIAN_SERVICE_KEY   service-account API key (or ATLASSIAN_EMAIL + ATLASSIAN_API_TOKEN)
#   WORKDIR                 where clones are kept between runs (default ./.kb-sync-clones)
#   STATE_FILE              sync cache (default ./kb-sync-state.json)
#   CLONE_ARGS              extra `git clone` args. Not --depth or --filter: repo_to_confluence.py
#                           reads every version of every file from the clone and skips shallow
#                           and partial clones
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
org="${1:?usage: sync_org.sh <github-org> [topic] [-- extra args]}"; shift
topic="kb-sync"
if [ $# -gt 0 ] && [ "$1" != "--" ]; then topic="$1"; shift; fi
if [ "${1:-}" = "--" ]; then shift; fi
workdir="${WORKDIR:-$PWD/.kb-sync-clones}"
mkdir -p "$workdir"
gh auth setup-git >/dev/null 2>&1 || true   # lets plain `git fetch` reuse GH_TOKEN

list_args=(--no-archived --limit 1000 --json nameWithOwner --jq '.[].nameWithOwner')
[ -n "$topic" ] && list_args=(--topic "$topic" "${list_args[@]}")
mapfile -t repos < <(gh repo list "$org" "${list_args[@]}")
# shellcheck disable=SC2016  # $topic is inside double quotes and does expand
echo "${#repos[@]} repositories in $org${topic:+ with topic '$topic'}"

repo_args=()
for r in "${repos[@]}"; do
  [ -n "$r" ] || continue
  dir="$workdir/${r//\//__}"
  if [ -d "$dir/.git" ]; then
    # full history + moved/new tags; origin/HEAD tracks a renamed default branch
    if ! git -C "$dir" fetch --quiet --prune --tags --force origin; then
      echo "::warning::fetch failed for $r, skipped"; continue
    fi
    git -C "$dir" remote set-head origin --auto >/dev/null 2>&1 || true
  else
    # shellcheck disable=SC2086
    if ! gh repo clone "$r" "$dir" -- --quiet ${CLONE_ARGS:-}; then
      echo "::warning::clone failed for $r, skipped"; continue
    fi
  fi
  repo_args+=(--repo "$dir")
done

if [ ${#repo_args[@]} -eq 0 ]; then
  echo "Nothing to sync."
  exit 0
fi

exec python3 "$here/repo_to_confluence.py" "${repo_args[@]}" \
  --space-key "${CONFLUENCE_SPACE_KEY:?set CONFLUENCE_SPACE_KEY}" \
  ${ATLASSIAN_CLOUD_ID:+--cloud-id "$ATLASSIAN_CLOUD_ID"} \
  --state-file "${STATE_FILE:-$PWD/kb-sync-state.json}" \
  "$@"
