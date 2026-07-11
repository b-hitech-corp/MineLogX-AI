# Git Conventions

---

## Branch Strategy (Git Flow)

```
master        # Production-ready code — protected, requires PR + approval
develop       # Integration branch — all features merge here first
release/*     # Release candidates — e.g. release/1.2.0
hotfix/*      # Emergency fixes branched from master
feature/*     # All new work — branched from develop
```

### Branch naming

```
feature/BHMIB-{ticket}-short-description

Examples:
  feature/BHMIB-57-opensearch-serverless-module
  feature/BHMIB-82-csv-vectorization-pipeline
  feature/BHMIB-91-bedrock-guardrails-cfn
  hotfix/BHMIB-99-fix-lambda-timeout
  release/1.2.0
```

Rules:
- Always branch from `develop` for features; from `master` for hotfixes
- Lowercase and hyphens only — no spaces or underscores
- Description: 3–5 words max

---

## Commit Message Format

```
[BHMIB-{ticket}] {type}: {short description}
```

### Types

| Type | Use for |
|---|---|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `chore` | Maintenance, dependency updates, config |
| `refactor` | Code restructure without behavior change |
| `docs` | Documentation only |
| `test` | Adding or updating tests |
| `infra` | Terraform or CloudFormation changes |
| `ci` | CI/CD pipeline changes |
| `fab` | Fabric task additions or changes |

### Examples

```
[BHMIB-57] feat: add OpenSearch Serverless Terraform module
[BHMIB-82] feat: implement CSV vectorization Step Functions state machine
[BHMIB-82] fix: correct S3 prefix routing from raw to approved
[BHMIB-91] infra: add Bedrock guardrail CloudFormation template
[BHMIB-103] fab: add health-check and restart-ollama tasks
[BHMIB-71] docs: add mkdocs-material site with C4 architecture pages
```

Rules:
- Subject line max 72 characters
- Use imperative mood: "add", "fix", "update" — not "added", "fixed", "updated"
- Reference the Jira ticket in every commit
- One logical change per commit
- Commit messages must be in **English**

---

## Pull Request Process

1. Branch from `develop` (or `master` for hotfixes)
2. Make atomic commits
3. Push and open PR against `develop`
4. PR title follows commit format: `[BHMIB-57] feat: add OpenSearch module`
5. PR description must include:
   - What changed and why
   - How to test/verify
   - Manual steps required for infrastructure changes
   - Fabric tasks affected
6. Squash merge into `develop` after approval

---

## Critical Rules

- **Never push directly to `master` or `develop`** — always use feature branches
- **Never commit** `.env`, `*.pem`, `terraform.tfstate`, `endpoints-*.md`, `PLAN-ARCHITECTURE-GOAL.md`
- **Never skip pre-commit hooks** (`--no-verify` is not allowed)
- **Always apply infrastructure changes through IaC** — no manual console changes in non-dev environments
