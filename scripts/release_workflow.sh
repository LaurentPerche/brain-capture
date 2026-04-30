#!/usr/bin/env bash
set -euo pipefail

# Bumps workflow version (minor by default), commits + tags it, rebuilds the
# .alfredworkflow bundle, and optionally opens it to trigger an Alfred import/update.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PART="${1:-minor}" # minor or major

NEW_VER="$("$ROOT_DIR/scripts/bump_version.sh" "$PART")"
TAG="v$NEW_VER"

"$ROOT_DIR/scripts/git.sh" add -A
"$ROOT_DIR/scripts/git.sh" commit -m "Release $TAG" >/dev/null || true
"$ROOT_DIR/scripts/git.sh" tag -a "$TAG" -m "Release $TAG" >/dev/null

BRAIN_CAPTURE_VERSION="$TAG" bash "$ROOT_DIR/scripts/build_workflow.sh" >/dev/null

OUT="$ROOT_DIR/dist/Brain Capture v${NEW_VER}.alfredworkflow"
echo "Released: $OUT"
echo "Next: push tag with: $ROOT_DIR/scripts/git.sh push origin $TAG"

if [[ "${BRAIN_CAPTURE_NO_OPEN:-0}" != "1" ]]; then
  # This triggers Alfred’s standard import/update UI.
  /usr/bin/open "$OUT" || true
fi
