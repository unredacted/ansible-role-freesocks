#!/usr/bin/env bash
# tests/run.sh - run all self-asserting role tests.
#
# These are offline tests (connection: local, no real hosts): the bootstrap and
# retirement contracts against tests/mock_fcp.py, the template renders and the
# address resolution, each with asserts.
#
# Usage: tests/run.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export ANSIBLE_ROLES_PATH="${ANSIBLE_ROLES_PATH:-$(dirname "$PWD")}"

shopt -s nullglob
tests=(tests/test_*.yml)
if [ ${#tests[@]} -eq 0 ]; then
  echo "No tests found."
  exit 0
fi

for t in "${tests[@]}"; do
  echo "=== ${t} ==="
  ansible-playbook "${t}"
done

echo "All tests passed."
