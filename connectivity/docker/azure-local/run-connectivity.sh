#!/usr/bin/env bash
set -euo pipefail

PYTHON=".venv/bin/python"
TARGET="${ATLANTICUS_AZURE_LOCAL_TARGET:-all}"

contains_target() {
  local target="$1"
  [[ "$TARGET" == "all" || ",$TARGET," == *",$target,"* ]]
}

run_integration() {
  local test_path="$1"
  "$PYTHON" - "$test_path" <<'PY'
from __future__ import annotations

import subprocess
import sys

path = sys.argv[1]
try:
    completed = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '-m', 'integration', path],
        check=False,
        timeout=60,
    )
except subprocess.TimeoutExpired:
    print(f'Azure-local integration timed out after 60 seconds: {path}', file=sys.stderr)
    raise SystemExit(124) from None

raise SystemExit(completed.returncode)
PY
}

"$PYTHON" docker/azure-local/provisioning/provision_connectivity.py

if contains_target "key-vault"; then
  run_integration "key-vault/tests/integration/azure_local"
fi

if contains_target "storage"; then
  run_integration "storage/tests/integration/azure_local"
fi

if contains_target "cosmos"; then
  run_integration "cosmos/tests/integration/azure_local"
fi

if contains_target "redis"; then
  run_integration "redis/tests/integration/azure_local"
fi
