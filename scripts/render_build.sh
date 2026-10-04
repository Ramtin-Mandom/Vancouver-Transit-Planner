#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
SNAPSHOT_PATH="${ROUTING_SNAPSHOT_PATH:-data/routing_snapshot}"

"$PYTHON_BIN" -m pip install -r requirements.txt

# Deploy the versioned offline artifact; no database is required during builds.
"$PYTHON_BIN" -m scripts.restore_routing_snapshot --output "$SNAPSHOT_PATH"
"$PYTHON_BIN" -m scripts.validate_routing_snapshot "$SNAPSHOT_PATH"

manifest="$SNAPSHOT_PATH/manifest.json"
if [[ ! -f "$manifest" ]]; then
  echo "ERROR: snapshot manifest was not generated at $manifest" >&2
  exit 1
fi

"$PYTHON_BIN" - "$manifest" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
snapshot_size = sum(path.stat().st_size for path in manifest_path.parent.iterdir())
build = manifest.get("build", {})
print(f"Render snapshot path: {manifest_path.parent}")
print(f"Render snapshot size: {snapshot_size} bytes")
print(f"Render snapshot counts: {manifest.get('counts', {})}")
print(f"Render snapshot build duration: {build.get('duration_seconds', 'unavailable')} seconds")
print(f"Render snapshot peak RSS: {build.get('peak_rss_bytes', 'unavailable')} bytes")
PY

echo "Render snapshot build and validation completed successfully."
