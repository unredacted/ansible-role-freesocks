#!/usr/bin/env bash
# tests/run.sh - run all self-asserting role tests.
#
# These are offline tests (connection: local, no real hosts / API calls): they
# render templates and exercise the role's filter expressions with asserts.
#
# Usage: tests/run.sh
set -euo pipefail
cd "$(dirname "$0")/.."

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
