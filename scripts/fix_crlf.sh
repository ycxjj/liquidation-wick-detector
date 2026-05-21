#!/bin/bash
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
fixed=0
while IFS= read -r -d '' f; do
  if grep -q $'\r' "$f" 2>/dev/null; then
    sed -i 's/\r$//' "$f"
    fixed=$((fixed + 1))
    echo "fixed: $f"
  fi
done < <(find scripts -name '*.sh' -type f -print0)
for f in start.sh start-gunicorn.sh fix.sh; do
  if [ -f "$ROOT/$f" ] && grep -q $'\r' "$ROOT/$f" 2>/dev/null; then
    sed -i 's/\r$//' "$ROOT/$f"
    fixed=$((fixed + 1))
    echo "fixed: $f"
  fi
done
chmod +x scripts/*.sh 2>/dev/null || true
chmod +x start.sh start-gunicorn.sh 2>/dev/null || true
echo "done. fixed ${fixed} file(s)."
