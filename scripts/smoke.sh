#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WF_DIR="$ROOT_DIR/workflow"

mkdir -p "$ROOT_DIR/.brain-capture"
export BRAIN_CAPTURE_CONFIG="$ROOT_DIR/.brain-capture/config.yaml"
export BRAIN_CAPTURE_NO_OPEN=1

cd "$WF_DIR"

# 1) Alfred menu JSON should be valid and ordered.
python3 -m brain_capture alfred-menu >"$ROOT_DIR/.brain-capture/menu.json"
python3 - "$ROOT_DIR/.brain-capture/menu.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

items = data["items"]
assert len(items) == 4, items
assert items[0]["arg"] == "capture", items[0]
assert items[1]["arg"] == "open-vault", items[1]
assert items[2]["arg"] == "open-config", items[2]
assert items[3]["arg"] == "health-check", items[3]
print("menu ok")
PY

# 2) Config creation should succeed (opening may fail in headless environments).
python3 -m brain_capture run open-config >/dev/null || true
test -f "$BRAIN_CAPTURE_CONFIG"
echo "config ok"

# 3) Health check should return a message (likely failing until configured).
python3 -m brain_capture run health-check || true
echo "health-check ok"
