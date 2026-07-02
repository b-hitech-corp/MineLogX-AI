#!/usr/bin/env bash
#
# setup-dev.sh — one-command developer setup for MineLogX-AI.
# Installs the Python env and wires the pre-commit git hook so linters run
# automatically on every commit. Run once after cloning.
#
# Usage:  bash scripts/setup-dev.sh
#
set -euo pipefail

echo "==> uv sync (Python env + deps)"
uv sync

echo "==> installing pre-commit as a uv tool (stable on PATH)"
uv tool install pre-commit --quiet || uv tool upgrade pre-commit

echo "==> installing the git pre-commit hook"
uv tool run pre-commit install

echo
echo "Done. Linters now run on every 'git commit'."
echo "Run all checks manually any time with:  uv tool run pre-commit run --all-files"
echo
echo "Optional (only if you work on that layer):"
echo "  - Terraform:  install terraform + run 'terraform fmt -recursive' / validate"
echo "  - Frontend:   install Node + pnpm (the eslint hook self-skips without pnpm)"
