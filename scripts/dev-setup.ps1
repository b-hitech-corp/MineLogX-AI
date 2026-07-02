<#
.SYNOPSIS
  One-command developer setup for MineLogX-AI (PowerShell).
  Installs the Python env and wires the pre-commit git hook. Run once after cloning.
.EXAMPLE
  ./scripts/setup-dev.ps1
#>
$ErrorActionPreference = "Stop"

Write-Host "==> uv sync (Python env + deps)"
uv sync

Write-Host "==> installing pre-commit as a uv tool (stable on PATH)"
try { uv tool install pre-commit } catch { uv tool upgrade pre-commit }

Write-Host "==> installing the git pre-commit hook"
uv tool run pre-commit install

Write-Host "`nDone. Linters now run on every 'git commit'."
Write-Host "Run all checks manually any time with:  uv tool run pre-commit run --all-files"
Write-Host "`nOptional (only if you work on that layer):"
Write-Host "  - Terraform: install terraform + run 'terraform fmt -recursive' / validate"
Write-Host "  - Frontend:  install Node + pnpm (the eslint hook self-skips without pnpm)"
