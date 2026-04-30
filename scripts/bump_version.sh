#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INIT_PY="$ROOT_DIR/workflow/brain_capture/__init__.py"
PLIST="$ROOT_DIR/workflow/info.plist"

PART="${1:-minor}" # minor (default) or major

python3 - "$INIT_PY" "$PLIST" "$PART" <<'PY'
import re
import sys
from pathlib import Path

init_path = Path(sys.argv[1])
plist_path = Path(sys.argv[2])
part = sys.argv[3]

init_text = init_path.read_text(encoding="utf-8")
m = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
if not m:
    raise SystemExit(f"Could not find __version__ in {init_path}")

cur = m.group(1).strip()
mm = re.fullmatch(r"(\d+)\.(\d+)", cur)
if not mm:
    raise SystemExit(f"Version must be MAJOR.MINOR (e.g. 1.0). Got: {cur}")

major, minor = int(mm.group(1)), int(mm.group(2))
if part == "major":
    major += 1
    minor = 0
elif part == "minor":
    minor += 1
else:
    raise SystemExit("Usage: scripts/bump_version.sh [minor|major]")

new_ver = f"{major}.{minor}"

init_new = re.sub(
    r'__version__\s*=\s*"([^"]+)"',
    f'__version__ = "{new_ver}"',
    init_text,
    count=1,
)
init_path.write_text(init_new, encoding="utf-8")

plist_text = plist_path.read_text(encoding="utf-8")
if "<key>version</key>" in plist_text:
    plist_new = re.sub(
        r"(<key>version</key>\s*<string>)([^<]*)(</string>)",
        rf"\g<1>{new_ver}\g<3>",
        plist_text,
        count=1,
    )
else:
    # Insert after <key>name</key>...</string> for discoverability.
    plist_new = re.sub(
        r"(<key>name</key>\s*<string>[^<]*</string>)",
        r"\1\n\t<key>version</key>\n\t<string>" + new_ver + r"</string>",
        plist_text,
        count=1,
    )
plist_path.write_text(plist_new, encoding="utf-8")

print(new_ver)
PY
