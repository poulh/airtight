#!/bin/bash
# Development setup for working on the plugin in this repo: a .venv with PyYAML.
# (Users of the plugin run /airtight:setup instead.) Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -e .
echo "ready. put the tools on PATH with:"
echo "  export PATH=\"$PWD/plugins/airtight/bin:\$PATH\""
