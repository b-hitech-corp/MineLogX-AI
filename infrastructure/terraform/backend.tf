# Terraform remote state — S3 backend with DynamoDB state locking.
#
# The state bucket and lock table must be bootstrapped ONCE before the first
# `terraform init` (see infrastructure/README.md → "Bootstrapping remote state").
# The block is commented so `terraform init` does not fail before bootstrap.
#
# NOTE: `key` should be scoped per environment. When using separate root modules
# under environments/, set a distinct key per env, e.g.
#   environments/_imported-poc/terraform.tfstate
#   environments/dev/terraform.tfstate
# Alternatively use Terraform workspaces for ephemeral per-developer state.
#
# terraform {
#   backend "s3" {
#     bucket         = "minelogx-terraform-state"
#     key            = "infrastructure/_imported-poc/terraform.tfstate"
#     region         = "us-east-1"
#     dynamodb_table = "minelogx-terraform-locks"
#     encrypt        = true
#   }
# }
