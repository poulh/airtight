#!/bin/bash
# Builds ui/.venv with the dashboard's dependencies. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -e .
echo "ready. from your project's directory run:"
echo "  $PWD/.venv/bin/airtight-ui            # watches .airtight/project.db"
echo "  $PWD/.venv/bin/airtight-ui --db PATH  # or any other database"
