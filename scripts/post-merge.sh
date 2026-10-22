#!/bin/bash
set -e

# AskJamie™ — post-merge setup
# Static HTML site — no build step required.
# Verifies key files exist, rebuilds search index, and runs all local gates.

echo "Post-merge: verifying static site integrity..."

for f in index.html assets/css/theme.css assets/js/app.js; do
  if [ ! -f "$f" ]; then
    echo "ERROR: required file missing: $f" >&2
    exit 1
  fi
done

echo "Post-merge: rebuilding search index..."
python3 scripts/build-search-index.py

echo "Post-merge: refreshing shared asset fingerprints and CSP..."
python3 scripts/cache-bust.py
python3 scripts/generate-csp.py

echo "Post-merge: running full site auditor..."
python3 scripts/audit-site.py --quiet
if [ $? -ne 0 ]; then
  echo "ERROR: Site audit failed — stale or broken pages detected." >&2
  exit 1
fi

echo "Post-merge: running site validator and link checker..."
python3 scripts/validate-site.py
python3 scripts/check-links.py >/dev/null

echo "Post-merge: checking shared asset fingerprints and Python regressions..."
python3 scripts/cache-bust.py --check
python3 -m pytest

echo "Post-merge: running browser responsive QA and JavaScript smoke tests..."

# Reuse the normal local preview server when one is already running. This
# keeps the hook compatible with the Replit workflow and avoids starting a
# second server on port 5000. In a shell-only environment, start one for both
# browser checks and clean it up when the hook exits.
BROWSER_BASE_URL="${BROWSER_BASE_URL:-http://127.0.0.1:5000}"
SERVER_PID=""
cleanup_browser_server() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup_browser_server EXIT

if curl --silent --fail "$BROWSER_BASE_URL/" >/dev/null 2>&1; then
  echo "Post-merge: reusing browser server at $BROWSER_BASE_URL."
else
  echo "Post-merge: starting temporary browser server at $BROWSER_BASE_URL..."
  python3 -m http.server 5000 --bind 127.0.0.1 >/tmp/askjamie-post-merge-server.log 2>&1 &
  SERVER_PID=$!

  for attempt in $(seq 1 20); do
    if curl --silent --fail "$BROWSER_BASE_URL/" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done

  if ! curl --silent --fail "$BROWSER_BASE_URL/" >/dev/null 2>&1; then
    echo "ERROR: temporary browser server did not become ready." >&2
    exit 1
  fi
fi

node scripts/responsive-qa.mjs --base="$BROWSER_BASE_URL"
BASE_URL="$BROWSER_BASE_URL" node tests/test_js_smoke.spec.mjs

echo "Post-merge: all checks passed."
