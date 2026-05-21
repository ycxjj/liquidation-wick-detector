#!/bin/bash
# 供 run_wickshield_*.sh source；勿直接执行
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

_wickshield_cli() {
  local cmd="$1"
  shift
  if python3 -m scripts.wickshield.cli "$cmd" -h 2>&1 | grep -q -- '--compact'; then
    python3 -m scripts.wickshield.cli "$cmd" "$@" --compact
  else
    python3 -m scripts.wickshield.cli "$cmd" "$@"
  fi
}
