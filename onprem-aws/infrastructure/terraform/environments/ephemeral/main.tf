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
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
  backend "s3" {} # partial — filled by fabfile; workspace isolates per-dev state
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
  build_pdf_layer     = var.build_pdf_layer
  build_csv_layer     = var.build_csv_layer
}
