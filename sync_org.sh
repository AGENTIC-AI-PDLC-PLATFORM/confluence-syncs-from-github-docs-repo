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
#   WORKDIR                 where clones are kept between runs (default ./.kb-sync-clones). A
#                           GitHub-hosted runner starts empty, so there every run clones afresh.
#   STATE_FILE              sync cache (default ./kb-sync-state.json)
#   CLONE_ARGS              extra `git clone` args. Not --depth or --filter: every version is read
#                           from the local clone, and repo_to_confluence.py refuses shallow or
#                           partial clones (their old versions would need GitHub during the sync).
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
# A failed listing fails the run. (Read through `mapfile < <(...)`, its exit status was lost: a
# rejected token gave "0 repositories ... Nothing to sync." and exit 0, a green run.)
if ! listing="$(gh repo list "$org" "${list_args[@]}")"; then
  echo "::error::cannot list the repositories of $org: check GH_TOKEN and the GitHub App installation" >&2
  exit 2
fi
repos=()
[ -z "$listing" ] || mapfile -t repos <<<"$listing"
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
  # shellcheck disable=SC2016  # $topic is inside double quotes and does expand
  echo "::warning::Nothing to sync: no repository of $org${topic:+ with topic '$topic'} could be cloned with this token."
  exit 0
fi

exec python3 "$here/repo_to_confluence.py" "${repo_args[@]}" \
  --space-key "${CONFLUENCE_SPACE_KEY:?set CONFLUENCE_SPACE_KEY}" \
  ${ATLASSIAN_CLOUD_ID:+--cloud-id "$ATLASSIAN_CLOUD_ID"} \
  --state-file "${STATE_FILE:-$PWD/kb-sync-state.json}" \
  "$@"
