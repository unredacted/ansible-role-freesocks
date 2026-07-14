#!/usr/bin/env bash
# One-command integration test: stand up the ephemeral Remnawave panel + the
# mock FCP + the mock Cloudflare API, run the task-level integration playbook,
# then (optionally) the REAL operation_mode=deploy playbook, then tear
# everything down (always).
#
#   bash tests/run_integration.sh                    # task-level phases only
#   RUN_DEPLOY_PHASE=1 bash tests/run_integration.sh # + the full real deploy
#
# The deploy phase runs the role exactly as production does (apt installs,
# /opt/remnanode, a real remnawave/node container the panel connects to), so it
# needs Linux + passwordless sudo — CI always sets RUN_DEPLOY_PHASE=1; locally
# leave it unset unless your sudo is passwordless.
#
# Requires Docker + ansible-core >= 2.15 + python3. Safe to run repeatedly
# (fresh panel state each time). This is what .github/workflows/ci.yml runs.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f tests/panel/docker-compose.yml)
FCP_MOCK_PORT="${FCP_MOCK_PORT:-8811}"
CF_MOCK_PORT="${CF_MOCK_PORT:-8812}"
RUN_DEPLOY_PHASE="${RUN_DEPLOY_PHASE:-0}"
FCP_PID=""
CF_PID=""

start_fcp_mock() {
  [ -n "$FCP_PID" ] && kill "$FCP_PID" 2>/dev/null || true
  python3 tests/mock_fcp.py "$FCP_MOCK_PORT" &
  FCP_PID=$!
  for _ in $(seq 1 20); do
    curl -sf "http://127.0.0.1:${FCP_MOCK_PORT}/__state" >/dev/null && return 0
    sleep 0.5
  done
  echo "[integration] mock FCP failed to start" >&2
  return 1
}

cleanup() {
  echo "[integration] tearing down"
  [ -n "$FCP_PID" ] && kill "$FCP_PID" 2>/dev/null || true
  [ -n "$CF_PID" ] && kill "$CF_PID" 2>/dev/null || true
  if [ "$RUN_DEPLOY_PHASE" = "1" ]; then
    # the deploy phase creates a real node install under /opt/remnanode
    sudo docker compose -f /opt/remnanode/docker-compose.yml down -v >/dev/null 2>&1 || true
    sudo rm -rf /opt/remnanode || true
  fi
  "${COMPOSE[@]}" down -v >/dev/null 2>&1 || true
  rm -rf /tmp/role-ci
}
trap cleanup EXIT

echo "[integration] starting the Remnawave test panel (pulls images on first run)"
"${COMPOSE[@]}" down -v >/dev/null 2>&1 || true # fresh state even after an aborted run
"${COMPOSE[@]}" up -d

echo "[integration] bootstrapping panel admin + minting an API token"
if ! BOOT="$(python3 tests/panel/bootstrap_panel.py)"; then
  echo "[integration] bootstrap failed — recent backend logs:" >&2
  "${COMPOSE[@]}" logs --tail=40 rw-test-backend >&2 || true
  exit 1
fi
set -a
eval "$BOOT"
FCP_MOCK_URL="http://127.0.0.1:${FCP_MOCK_PORT}"
CF_MOCK_URL="http://127.0.0.1:${CF_MOCK_PORT}"
set +a
echo "[integration] panel ready at ${REMNAWAVE_TEST_URL}"

echo "[integration] starting the mock FCP on :${FCP_MOCK_PORT} + mock Cloudflare on :${CF_MOCK_PORT}"
start_fcp_mock
python3 tests/mock_cloudflare.py "$CF_MOCK_PORT" &
CF_PID=$!
for _ in $(seq 1 20); do
  curl -sf "${CF_MOCK_URL}/__state" >/dev/null && break
  sleep 0.5
done

# The repo dir itself is the role; resolve it by name from the parent dir.
export ANSIBLE_ROLES_PATH="$(dirname "$PWD")"

echo "[integration] running the task-level integration playbook"
ansible-playbook tests/test_integration.yml "$@"

if [ "$RUN_DEPLOY_PHASE" = "1" ]; then
  echo "[integration] restarting the mock FCP (clean state for the deploy phase)"
  start_fcp_mock
  echo "[integration] running the REAL operation_mode=deploy playbook"
  ansible-playbook tests/test_deploy.yml "$@"
else
  echo "[integration] skipping the real-deploy phase (set RUN_DEPLOY_PHASE=1; needs Linux + passwordless sudo)"
fi

echo "[integration] PASSED"
