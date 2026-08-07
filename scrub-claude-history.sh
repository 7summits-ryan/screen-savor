#!/bin/bash
# One-shot: strip every Claude/Anthropic mention from commit messages and
# normalise authorship to ryan@7summits.software across the whole history.
#
# File CONTENT is already clean at every commit - only the messages of cc9f3e3
# and d03e5bf carry trailers - so this rewrites messages and identity only, and
# every tree comes through byte-identical.
#
# Rewrites published history. The backup branch backup-pre-scrub-20260806 is
# left pointing at the old commits; recovery is at the bottom of this file.
set -euo pipefail
cd "$(dirname "$0")"

if ! git rev-parse --verify -q backup-pre-scrub-20260806 >/dev/null; then
    echo "backup branch is missing - refusing to rewrite" >&2
    exit 1
fi

# Only 'main' is passed, so the backup branch is deliberately NOT rewritten and
# keeps the old commits reachable. --tag-name-filter moves v0.1.0 and v0.1.1
# onto their rewritten commits; the annotated v0.1.1 is recreated with the
# repo's current identity, which is already ryan@7summits.software.
FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f \
  --env-filter '
    export GIT_AUTHOR_NAME="Ryan Tait"
    export GIT_AUTHOR_EMAIL="ryan@7summits.software"
    export GIT_COMMITTER_NAME="Ryan Tait"
    export GIT_COMMITTER_EMAIL="ryan@7summits.software"
  ' \
  --msg-filter '
    sed -e "/[Cc]laude/d" -e "/[Aa]nthropic/d" \
      | sed -e :a -e "/^\n*$/{\$d;N;};/\n\$/ba"
  ' \
  --tag-name-filter cat -- main

echo
echo "=== rewritten history ==="
git log --format='%h | %an <%ae> | %s' main

echo
echo "=== verifying no mention survives ==="
if git log --format='%B' main | grep -inE 'claude|anthropic'; then
    echo "STILL PRESENT - do not push" >&2
    exit 1
fi
echo "clean: no claude/anthropic in any commit message on main"

echo
echo "=== verifying trees are unchanged vs the backup ==="
if [ -z "$(git diff backup-pre-scrub-20260806 main)" ]; then
    echo "clean: working tree content is byte-identical to before the rewrite"
else
    echo "TREE CHANGED - do not push, investigate" >&2
    exit 1
fi

cat <<'NOTE'

Nothing has been pushed yet. Review the output above, then publish with:

    git push --force-with-lease origin main
    git push --force origin v0.1.0 v0.1.1

(--force-with-lease refuses if someone else pushed since; the tags need plain
--force because they are being moved to different commits.)

If GitHub rejects it, branch protection on main is on - turn it off for the
push under Settings > Branches, then re-enable it.

Once you are satisfied, drop the safety nets:

    git update-ref -d refs/original/refs/heads/main
    git branch -D backup-pre-scrub-20260806
    rm scrub-claude-history.sh

TO UNDO, as long as the backup branch still exists:

    git reset --hard backup-pre-scrub-20260806
    git tag -f v0.1.0 6ee8f37 && git tag -f v0.1.1 cc9f3e3
    git push --force origin main v0.1.0 v0.1.1
NOTE
