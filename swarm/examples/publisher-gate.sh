#!/usr/bin/env bash
# Where the publication check goes in an owner-run publisher: after its other gates, before
# its first push. This is a minimal publisher used by the tests; the three marked lines are
# what an existing script (for example open-overnight-prs.sh) needs.
#
#   publisher-gate.sh REPO BASE_SHA BRANCH PR_BODY_FILE [--allow-ai-attribution]
#
# Environment: EXPECT_AUTHOR_EMAIL (required), TEST_CMD (default: true), GH (default: gh),
# GH_REPO (owner/name for `gh pr create`), PAI_SWARM (default: the directory above this file).
set -euo pipefail
die() { echo "publisher: $*" >&2; exit 1; }
[ $# -ge 4 ] || { sed -n '6p' "$0" >&2; exit 2; }
REPO=$1 BASE=$2 BRANCH=$3 BODY=$4
shift 4
PAI_SWARM=${PAI_SWARM:-$(cd "$(dirname "$0")/.." && pwd -P)}
HEAD=$(git -C "$REPO" rev-parse --verify "refs/heads/$BRANCH^{commit}") || die "no branch $BRANCH"

# Gates a publisher usually already has. None of them reads the commit message.
[ -z "$(git -C "$REPO" status --porcelain)" ] || die "the working tree is not clean"
[ "$(git -C "$REPO" log -1 --format=%ae "$HEAD")" = "${EXPECT_AUTHOR_EMAIL:?}" ] || die "unexpected commit author"
(cd "$REPO" && ${TEST_CMD:-true}) || die "tests failed"

# >>> publication check: the commits that are about to be pushed and the exact PR body.
# >>> "$@" passes --allow-ai-attribution through only when the owner typed it for this run.
python3 "$PAI_SWARM/publish_check.py" --repo "$REPO" --range "$BASE..$HEAD" --pr-body-file "$BODY" "$@" ||
  die "publication check failed; nothing was pushed"

git -C "$REPO" push --quiet origin "$HEAD:refs/heads/$BRANCH"   # the first push; never forced
"${GH:-gh}" pr create --repo "${GH_REPO:?}" --base main --head "$BRANCH" \
  --title "$(git -C "$REPO" log -1 --format=%s "$HEAD")" --body-file "$BODY"
