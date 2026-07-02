# Contributing to MineLogX-AI

Dev onboarding and day-to-day workflow. For architecture and the full IaC/agent
rules, see [`CLAUDE.md`](CLAUDE.md) and [`AGENTS.md`](AGENTS.md).

## Prerequisites

| Tool | For | Notes |
|------|-----|-------|
| [uv](https://docs.astral.sh/uv/) | Python env & tooling | Python **>= 3.11** (pinned in `.python-version`) |
| AWS CLI v2 | AWS access / discovery | SSO login |
| Terraform >= 1.5 | IaC (`terraform` hooks, `fab env.*`) | only if you touch `infrastructure/terraform` |
| Node 20 + pnpm | frontend lint/build | only if you touch `frontend/` |

## 1. Repo setup — one command

```bash
./scripts/dev-setup.sh          # PowerShell: ./scripts/dev-setup.ps1
```

Creates the `.venv`, installs deps (incl. pre-commit), and installs the git hooks
so linters (ruff, bandit, yamllint, cfn-lint, eslint) run on **every
commit** automatically. Idempotent — safe to re-run. After this, just commit.

Run all hooks manually any time: `uv run pre-commit run --all-files`.

## 2. AWS access (SSO)

Use a **dedicated named profile** so other projects aren't affected. Scope it
per shell — don't set it globally.

```bash
export AWS_PROFILE=125396563242_B_Hitech-586928288932   # PowerShell: $env:AWS_PROFILE = "..."
aws sso login --profile 125396563242_B_Hitech-586928288932
aws sts get-caller-identity                             # verify Account 125396563242
```

The SSO token lasts the session; re-run `aws sso login` when it expires.

## 3. Snapshot the deployed POC (Phase 2 — import)

```bash
bash onprem-aws/scripts/discover-aws.sh    # PowerShell: ./onprem-aws/scripts/discover-aws.ps1
```

Read-only. Output lands in `onprem-aws/infrastructure/discovery/` (gitignored — it holds
account IDs/ARNs). Do not commit it.

## 4. Development environments (Fabric)

Each dev gets an isolated stack; pick the engine with `--engine`.

```bash
uv run fab env.up   --env=dev-<user> --engine=terraform      # or --engine=cloudformation
uv run fab env.plan --env=dev-<user>                         # preview only
uv run fab env.list                                          # active workspaces + stacks
uv run fab env.down --env=dev-<user>                         # tear down (prod is guarded)
```

Fixed environments are `dev` / `qa` / `prod`; ephemeral ones are
`dev-<user>`. See the IaC Strategy and ownership rules in `CLAUDE.md`.

## 5. Branches, commits, PRs

- Git Flow; branch names and the `[BHMIB-<ticket>] <type>: <desc>` commit format
  are defined in [`CLAUDE.md`](CLAUDE.md#git-workflow--git-flow).
- Open PRs/issues with the provided templates (`.github/`).
- CI (`.github/workflows/lint.yml`) runs ruff, bandit, pip-audit, yamllint,
  gitleaks, and eslint/tsc — same linters as the local pre-commit hooks.

## Infra change rule

Infrastructure is defined in **both** Terraform and CloudFormation, kept at
parity, and applied only through Fabric — never manual console changes. Details
in `CLAUDE.md` → *IaC Strategy*.
