#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
SNAPSHOT_PATH="${ROUTING_SNAPSHOT_PATH:-data/routing_snapshot}"

"$PYTHON_BIN" -m pip install -r requirements.txt

required_database_variables=(DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD)
for variable in "${required_database_variables[@]}"; do
  if [[ -z "${!variable:-}" ]]; then
    echo "ERROR: required database environment variable ${variable} is not set." >&2
    exit 1
  fi
done
echo "Required database environment variables are configured."

build_arguments=(--output "$SNAPSHOT_PATH")
if [[ -n "${ROUTING_SNAPSHOT_FIXTURE_PATH:-}" ]]; then
  build_arguments+=(--fixture-json "$ROUTING_SNAPSHOT_FIXTURE_PATH")
fi
"$PYTHON_BIN" -m scripts.build_routing_snapshot "${build_arguments[@]}"
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
