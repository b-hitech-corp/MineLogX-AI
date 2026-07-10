# Prerequisites

Install these tools before running any Fabric task or environment operation.

| Tool | Required for | Notes |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | Python env & all tooling | Requires Python **>= 3.11** (pinned in `.python-version`) |
| AWS CLI v2 | AWS access / SSO login | Must be v2 — v1 does not support SSO |
| Terraform >= 1.5 | IaC (only if you touch `infrastructure/terraform/`) | Optional for CloudFormation-only workflows |
| Node 20 + pnpm | Frontend build | Only if you modify `shared/frontend/` |

---

## Installing uv

```bash
# macOS / Linux
curl -Ls https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

After install, uv handles the `.venv` and all Python dependencies automatically.
You do **not** need to activate a virtualenv — prefix commands with `uv run`.

---

## Installing AWS CLI v2

Follow the [official AWS docs](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) for your OS. Verify:

```bash
aws --version
# aws-cli/2.x.x ...
```

---

## Installing Terraform

Only needed if you work on the Terraform side of the IaC. CloudFormation workflows
are fully driven by Fabric without Terraform.

```bash
# macOS via Homebrew
brew install terraform

# Windows via Chocolatey
choco install terraform
```

Verify: `terraform --version` should show >= 1.5.

---

## Installing Node + pnpm

Only needed to build or develop the React frontend in `shared/frontend/`.

```bash
# Install Node 20 (use nvm or fnm for version management)
nvm install 20
nvm use 20

# Install pnpm globally
npm install -g pnpm
```

---

!!! tip "One-command setup"
    After cloning the repo, `./scripts/dev-setup.sh` (or `./scripts/dev-setup.ps1` on Windows)
    installs all Python dependencies and wires pre-commit hooks in one shot.
