<#
.SYNOPSIS
  One-shot developer bootstrap (Windows/PowerShell). Run once after cloning.
    ./scripts/dev-setup.ps1
  Idempotent. Sets up the Python env and the pre-commit git hook so linters run
  on every commit automatically.
#>
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")  # repo root

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "ERROR: 'uv' is not installed."
  Write-Host "  Install it: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
}

Write-Host "==> uv sync (create .venv + install deps incl. pre-commit)"
uv sync

Write-Host "==> installing git hooks (pre-commit)"
uv run pre-commit install --install-hooks

if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) { Write-Host "  note: 'terraform' not found — needed for infra work." }
if (-not (Get-Command aws -ErrorAction SilentlyContinue))       { Write-Host "  note: 'aws' CLI not found — needed for AWS access." }

Write-Host ""
Write-Host "Done. Hooks are active — just make your changes and commit."
Write-Host "Run all checks manually anytime: uv run pre-commit run --all-files"
