#!/usr/bin/env bash
# tests/run.sh - run all self-asserting role tests.
#
# These are offline tests (connection: local, no real hosts / API calls): they
# render templates and exercise the role's filter expressions with asserts.
#
# Usage: tests/run.sh
set -euo pipefail
cd "$(dirname "$0")/.."
# test_fcp_managed.yml includes task files through the role (include_role).
export ANSIBLE_ROLES_PATH="${ANSIBLE_ROLES_PATH:-$(dirname "$PWD")}"

shopt -s nullglob
# test_integration.yml / test_deploy.yml need the live panel + mocks — they run
# via tests/run_integration.sh, not here.
tests=()
for t in tests/test_*.yml; do
  case "$t" in
    tests/test_integration.yml|tests/test_deploy.yml) continue ;;
  esac
  tests+=("$t")
done
if [ ${#tests[@]} -eq 0 ]; then
  echo "No tests found."
  exit 0
fi

for t in "${tests[@]}"; do
  echo "=== ${t} ==="
  ansible-playbook "${t}"
done

echo "All tests passed."
