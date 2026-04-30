#!/usr/bin/env bash
set -euo pipefail

# Git wrapper: if this workspace uses a separate git directory (gitdir/),
# run git with that metadata location. Otherwise, run git normally.
#
# This exists because some environments disallow writing to .git/.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -d "$ROOT_DIR/gitdir" ]]; then
  exec git --git-dir "$ROOT_DIR/gitdir" --work-tree "$ROOT_DIR" "$@"
fi

exec git "$@"

