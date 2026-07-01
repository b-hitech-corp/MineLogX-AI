# Ephemeral per-developer environment — thin wrapper over modules/env_stack.
# Isolated by Terraform WORKSPACE (one per dev, e.g. dev-cesar). Fabric passes
# -var name_prefix / -var environment; defaults below are placeholders.
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Ephemeral state uses workspaces (not a per-env key) — see ../../backend.tf.
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      "aws-apn-id" = var.project_apn_id
      Project      = "MineLogX-AI"
      Environment  = var.environment
      ManagedBy    = "terraform"
    }
  }
}

module "stack" {
  source              = "../../modules/env_stack"
  name_prefix         = var.name_prefix
  enable_llm_fallback = var.enable_llm_fallback
  ssh_ingress_cidrs   = var.ssh_ingress_cidrs
  ec2_key_name        = var.ec2_key_name
}
