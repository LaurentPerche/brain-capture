#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW_DIR="$ROOT_DIR/workflow"
DIST_DIR="$ROOT_DIR/dist"

mkdir -p "$DIST_DIR"

git_cmd() {
  if [[ -d "$ROOT_DIR/gitdir" ]]; then
    git --git-dir "$ROOT_DIR/gitdir" --work-tree "$ROOT_DIR" "$@"
  else
    git "$@"
  fi
}

strip_v() {
  local v="$1"
  v="${v#refs/tags/}"
  v="${v#v}"
  echo "$v"
}

VERSION="${BRAIN_CAPTURE_VERSION:-}"
if [[ -z "$VERSION" ]]; then
  VERSION="$(git_cmd describe --tags --exact-match 2>/dev/null || true)"
fi
if [[ -z "$VERSION" ]]; then
  VERSION="$(
    python3 -c 'import re; p="workflow/brain_capture/__init__.py"; s=open(p,"r",encoding="utf-8").read(); m=re.search(r"__version__\s*=\s*\"([^\"]+)\"", s); print(m.group(1) if m else "0.0")' \
      2>/dev/null || echo "0.0"
  )"
fi
VERSION="$(strip_v "$VERSION")"

OUT_VERSIONED="$DIST_DIR/Brain Capture v${VERSION}.alfredworkflow"
OUT_LATEST="$DIST_DIR/Brain Capture.alfredworkflow"
rm -f "$OUT_VERSIONED" "$OUT_LATEST"

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

STAGE_DIR="$TMP_DIR/workflow"
mkdir -p "$STAGE_DIR"
cp -R "$WORKFLOW_DIR/"* "$STAGE_DIR/"

# Patch version into the staged workflow without dirtying the git working tree.
python3 - "$STAGE_DIR/info.plist" "$STAGE_DIR/brain_capture/__init__.py" "$VERSION" <<'PY'
import re
import sys
from pathlib import Path

plist_path = Path(sys.argv[1])
init_path = Path(sys.argv[2])
ver = sys.argv[3]

plist_text = plist_path.read_text(encoding="utf-8")
if "<key>version</key>" in plist_text:
    plist_text = re.sub(
        r"(<key>version</key>\s*<string>)([^<]*)(</string>)",
        rf"\g<1>{ver}\g<3>",
        plist_text,
        count=1,
    )
else:
    plist_text = re.sub(
        r"(<key>name</key>\s*<string>[^<]*</string>)",
        r"\1\n\t<key>version</key>\n\t<string>" + ver + r"</string>",
        plist_text,
        count=1,
    )
plist_path.write_text(plist_text, encoding="utf-8")

init_text = init_path.read_text(encoding="utf-8")
init_text = re.sub(
    r'__version__\s*=\s*"([^"]+)"',
    f'__version__ = "{ver}"',
    init_text,
    count=1,
)
init_path.write_text(init_text, encoding="utf-8")
PY

# Alfred workflows are zip files with a .alfredworkflow extension.
(cd "$STAGE_DIR" && zip -qr "$OUT_VERSIONED" .)
cp -f "$OUT_VERSIONED" "$OUT_LATEST"

echo "Built: $OUT_VERSIONED"
echo "Built: $OUT_LATEST"
