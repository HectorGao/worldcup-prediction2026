#!/bin/sh

set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

echo "Code and configuration changes"
git status --short -- \
  backend src scripts tests \
  pyproject.toml package.json render.yaml Procfile runtime.txt \
  index.html .env.example .gitignore .github \
  README.md DATA.md CONTRIBUTING.md SECURITY.md CODE_OF_CONDUCT.md LICENSE

echo
echo "Data changes"
git status --short -- data outputs precomputed

echo
echo "Other worktree changes"
git status --short

echo
echo "Repository safety"
scripts/check_repository_safety.sh

echo
echo "SQLite integrity"
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 data/worldcup.sqlite3 'PRAGMA integrity_check;'
else
  echo "sqlite3 CLI is unavailable; the repository safety check skipped the direct CLI result."
fi

echo
echo "Test suite"
.venv/bin/pytest -q

echo
echo "Static export"
PYTHONPATH=backend .venv/bin/python scripts/export_static_site.py

echo
echo "Review complete. Inspect every changed path and stage only the intended public files."
