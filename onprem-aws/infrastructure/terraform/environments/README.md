# Terraform environments (root modules)

Each subfolder is a root module with its own state key (see `../backend.tf`).

| Environment      | Purpose                                                        | State key                                   |
|------------------|----------------------------------------------------------------|---------------------------------------------|
| `_imported-poc/` | Adopts the hand-deployed POC via import blocks. Source of truth.| `infrastructure/_imported-poc/terraform.tfstate` |
| `dev/`           | Fixed shared dev environment.                                  | `infrastructure/dev/terraform.tfstate`      |
| `qa/`       | Fixed shared qa environment.                              | `infrastructure/qa/terraform.tfstate`  |
| `prod/`          | Fixed shared production environment.                           | `infrastructure/prod/terraform.tfstate`     |
| `ephemeral/`     | Per-developer disposable env. Parameterized by `environment`.  | Terraform workspace `dev-<user>`            |

Ephemeral environments are created/destroyed via Fabric:

```bash
fab env.up   --env=dev-cesar --engine=terraform
fab env.down --env=dev-cesar
```

Fixed environments and the POC import are also driven through Fabric so the
workflow is identical for everyone (`fab env.plan --env=qa`, etc.).
