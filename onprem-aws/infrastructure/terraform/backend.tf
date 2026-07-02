# Terraform remote state — S3 backend with DynamoDB state locking.
#
# The state bucket and lock table must be bootstrapped ONCE before the first
# `terraform init` (see infrastructure/README.md → "Bootstrapping remote state").
# The block is commented so `terraform init` does not fail before bootstrap.
#
# Account strategy (current): dev / qa / prod all live in the SAME AWS account
# (586928288932) for now, isolated by resource name prefix `minelogx-<env>` and a
# SEPARATE state key per environment (below). PROD will move to its own AWS
# account later; when it does, give prod its own backend bucket/account and keep
# dev/qa where they are.
#
# NOTE: `key` is scoped per environment — one distinct state key each, e.g.
#   onprem-aws/_imported-demo/terraform.tfstate
#   onprem-aws/dev/terraform.tfstate
#   onprem-aws/qa/terraform.tfstate
#   onprem-aws/prod/terraform.tfstate   # → separate bucket/account in the future
# Ephemeral per-developer envs use Terraform workspaces, not separate keys.
#
# terraform {
#   backend "s3" {
#     bucket         = "minelogx-terraform-state"
#     key            = "onprem-aws/_imported-demo/terraform.tfstate"
#     region         = "us-east-1"
#     dynamodb_table = "minelogx-terraform-locks"
#     encrypt        = true
#   }
# }
