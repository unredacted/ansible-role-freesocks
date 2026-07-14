#!/usr/bin/env bash
# One-command integration test: stand up the ephemeral Remnawave panel + the
# mock FCP, run tests/test_integration.yml, then tear everything down (always).
#
#   bash tests/run_integration.sh
#
# Requires Docker + ansible-core >= 2.15 + python3. Safe to run repeatedly
# (fresh panel state each time). This is what .github/workflows/ci.yml runs.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f tests/panel/docker-compose.yml)
MOCK_PORT="${FCP_MOCK_PORT:-8811}"
MOCK_PID=""

cleanup() {
  echo "[integration] tearing down"
  [ -n "$MOCK_PID" ] && kill "$MOCK_PID" 2>/dev/null || true
  "${COMPOSE[@]}" down -v >/dev/null 2>&1 || true
  rm -rf /tmp/role-ci
}
trap cleanup EXIT

echo "[integration] starting the Remnawave test panel (pulls images on first run)"
"${COMPOSE[@]}" down -v >/dev/null 2>&1 || true   # fresh state even after an aborted run
"${COMPOSE[@]}" up -d

echo "[integration] bootstrapping panel admin + minting an API token"
if ! BOOT="$(python3 tests/panel/bootstrap_panel.py)"; then
  echo "[integration] bootstrap failed — recent backend logs:" >&2
  "${COMPOSE[@]}" logs --tail=40 rw-test-backend >&2 || true
  exit 1
fi
set -a
eval "$BOOT"
FCP_MOCK_URL="http://127.0.0.1:${MOCK_PORT}"
set +a
echo "[integration] panel ready at ${REMNAWAVE_TEST_URL}"

echo "[integration] starting the mock FCP on :${MOCK_PORT}"
python3 tests/mock_fcp.py "$MOCK_PORT" &
MOCK_PID=$!
for _ in $(seq 1 20); do
  curl -sf "${FCP_MOCK_URL}/__state" >/dev/null && break
  sleep 0.5
done

echo "[integration] running the integration playbook"
# The repo dir itself is the role; resolve it by name from the parent dir.
ANSIBLE_ROLES_PATH="$(dirname "$PWD")" \
  ansible-playbook tests/test_integration.yml "$@"

echo "[integration] PASSED"
