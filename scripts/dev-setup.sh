#!/usr/bin/env bash
#
# One-shot developer bootstrap. Run once after cloning; then just commit.
#   ./scripts/dev-setup.sh
#
# Idempotent: safe to re-run. Sets up the Python env and the pre-commit git hook
# so linters (ruff, bandit, yamllint, terraform, cfn-lint, eslint) run on every
# commit automatically.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # repo root

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: 'uv' is not installed."
  echo "  Install it: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

echo "==> uv sync (create .venv + install deps incl. pre-commit)"
uv sync

echo "==> installing git hooks (pre-commit)"
uv run pre-commit install --install-hooks

# Non-blocking checks for infra tooling.
command -v terraform >/dev/null 2>&1 || echo "  note: 'terraform' not found — needed for infra work."
command -v aws        >/dev/null 2>&1 || echo "  note: 'aws' CLI not found — needed for AWS access."

echo
echo "Done. Hooks are active — just make your changes and commit."
echo "Run all checks manually anytime: uv run pre-commit run --all-files"
