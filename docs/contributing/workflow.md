# Contributing Workflow

---

## 1. Repo setup (one command)

```bash
./scripts/dev-setup.sh          # PowerShell: ./scripts/dev-setup.ps1
```

Creates the `.venv`, installs deps (including pre-commit), and installs the git hooks
so linters (ruff, bandit, yamllint, cfn-lint) run on every commit automatically. Idempotent — safe to re-run.

Run all hooks manually: `uv run pre-commit run --all-files`.

---

## 2. AWS access (SSO)

Use a **dedicated named profile** — don't set it globally:

```bash
export AWS_PROFILE=minelogx-admin          # PowerShell: $env:AWS_PROFILE = "minelogx-admin"
aws sso login --profile minelogx-admin
aws sts get-caller-identity                # verify Account: 586928288932
```

The SSO token lasts the session. Re-run `aws sso login` when it expires (~8h).

---

## 3. Development environments

```bash
# Stand up your own ephemeral env
uv run fab env.up dev-<yourname> --seed

# Deploy changes
uv run fab env.up dev-<yourname>           # full stack
uv run fab lambda.redeploy api dev-<yourname>  # code-only (no layer rebuild)

# Tear down when done
uv run fab env.down dev-<yourname>
```

---

## 4. Pre-commit hooks

Hooks run automatically on `git commit`. They include:

| Hook | What it checks |
|---|---|
| `ruff` | Python linting and formatting |
| `bandit -ll` | Security vulnerabilities (MEDIUM+ severity) |
| `yamllint` | YAML syntax and style |
| `cfn-lint` | CloudFormation template validity |

If a hook fails, fix the issue and re-commit. Do not use `--no-verify`.

---

## 5. Opening a PR

1. Branch from `master` for features (or `main` for hotfixes)
2. Make atomic commits following the commit format (see [Git Conventions](git-conventions.md))
3. Push and open PR against `master`
4. PR description must include: what changed and why, how to test, any manual infra steps

---

## IaC change checklist

When modifying CloudFormation or Terraform:

- [ ] Update **both** CloudFormation and Terraform definitions (parity rule)
- [ ] `aws cloudformation validate-template` passes
- [ ] `terraform validate` + `terraform fmt -recursive` passes
- [ ] `uv run fab env.plan dev` shows expected changes only
- [ ] `uv run fab env.health dev` shows all green after deploy

---

## File modification boundaries

| Tier | Files |
|---|---|
| ✅ Free to modify | `onprem-aws/infrastructure/cloudformation/**`, `onprem-aws/backend/lambdas/**`, `shared/**`, `fabfile.py`, `*.md` |
| ⚠️ Ask first | `terraform/versions.tf`, `terraform/environments/prod/**`, `terraform/environments/_imported-demo/**`, any IAM policy |
| ❌ Never modify | `.env` files, `*.pem`, `terraform.tfstate`, `onprem-aws/infrastructure/discovery/**` |
