#!/bin/sh
# Rebuild the `develop` branch: upstream master plus the fork branches listed in ci/branches.txt,
# merged in order. Only commits by the fork owner are accepted.
#
# Usage: ci/build-develop.sh [upstream-remote] [fork-remote]   (defaults: upstream origin)
# Environment:
#   ALLOWED_EMAIL  author and committer e-mail every merged commit must carry
#                  (default: the fork owner's GitHub noreply address)
#   BRANCHES_FILE  allow-list (default: ci/branches.txt next to this script)
#
# The result is left on a local branch `develop`. Merge commits take the newest committer date of
# their inputs, so an unchanged input set gives the same develop SHA on every run.
# Exit status: 0 all merged; 2 one or more branches skipped (conflict or missing); 1 refusal.

set -u
UP=${1:-upstream}
FORK=${2:-origin}
ALLOWED_EMAIL=${ALLOWED_EMAIL:-69278611+JohnsterID@users.noreply.github.com}
HERE=$(cd "$(dirname "$0")" && pwd)
BRANCHES_FILE=${BRANCHES_FILE:-$HERE/branches.txt}
LIST=$(sed -e 's/#.*//' -e 's/[[:space:]]*$//' "$BRANCHES_FILE" | grep -v '^$')

git fetch -q "$UP" master || exit 1
git fetch -q "$FORK" || exit 1
BASE=$(git rev-parse "$UP/master") || exit 1

# Refuse before touching anything if a listed branch carries someone else's commit.
bad=0
for b in $LIST; do
  git rev-parse -q --verify "$FORK/$b^{commit}" >/dev/null || continue
  others=$(git log --no-merges --format='%H %ae %ce' "$BASE..$FORK/$b" | awk -v e="$ALLOWED_EMAIL" '$2 != e || $3 != e')
  if [ -n "$others" ]; then
    echo "REFUSED $b: commits not authored and committed by $ALLOWED_EMAIL:"; echo "$others"
    bad=1
  fi
  if git log --format=%B "$BASE..$FORK/$b" | grep -qi '^co-authored-by:'; then
    echo "REFUSED $b: Co-authored-by trailer"; bad=1
  fi
done
[ $bad -eq 0 ] || exit 1

git checkout -q --detach "$BASE" || exit 1
skipped=0
for b in $LIST; do
  if ! git rev-parse -q --verify "$FORK/$b^{commit}" >/dev/null; then
    echo "SKIPPED $b: not on $FORK (deleted after merge upstream?)"; skipped=1; continue
  fi
  if [ -z "$(git cherry HEAD "$FORK/$b" | grep '^+')" ]; then
    echo "already in: $b"; continue
  fi
  d=$( (git log -1 --format=%ct HEAD; git log -1 --format=%ct "$FORK/$b") | sort -n | tail -1)
  if GIT_AUTHOR_DATE="@$d +0000" GIT_COMMITTER_DATE="@$d +0000" \
     git merge -q --no-ff --no-edit -m "Merge branch '$b' into develop" "$FORK/$b" >/dev/null 2>&1; then
    echo "merged: $b ($(git rev-parse --short "$FORK/$b"))"
  else
    echo "SKIPPED $b: conflicts in $(git diff --name-only --diff-filter=U | tr '\n' ' ')"
    git merge --abort; skipped=1
  fi
done
git checkout -q -B develop
echo "develop = $(git rev-parse HEAD) on $UP/master $(git rev-parse --short "$BASE")"
[ $skipped -eq 0 ] || exit 2
