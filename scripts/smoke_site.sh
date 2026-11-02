#!/usr/bin/env bash
# Checks a deployed static site answers like production should: the page with its
# security headers, Cesium's files, the data for the expected run, the browser
# simulator's runtime. Usage: scripts/smoke_site.sh <site url> <expected run id>
set -euo pipefail
site="${1%/}"
want="$2"

# Pages can take a few seconds to serve a new deployment everywhere.
for i in $(seq 1 30); do
  got=$(curl -fsS "$site/data/index.json" | python3 -c "import sys, json; print(json.load(sys.stdin)['run_id'])" 2>/dev/null || true)
  [ "$got" = "$want" ] && break
  sleep 10
done
[ "$got" = "$want" ] || { echo "::error::$site serves run '${got:-nothing}', expected $want"; exit 1; }

headers=$(curl -fsS -D - -o /dev/null "$site/" | tr -d '\r')
echo "$headers" | grep -qi "^content-security-policy:.*'wasm-unsafe-eval'" || { echo "::error::No content security policy on $site"; exit 1; }
echo "$headers" | grep -qi '^x-content-type-options: nosniff' || { echo "::error::Missing X-Content-Type-Options on $site"; exit 1; }
for path in cesium/Assets/approximateTerrainHeights.json data/meta.json data/conjunctions.json pyodide/pyodide.mjs pyodide/pyodide.asm.wasm; do
  curl -fsS -o /dev/null "$site/$path" || { echo "::error::$site/$path does not load"; exit 1; }
done
echo "$site serves run $want with its headers, data and simulator."
