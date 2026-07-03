# Environment root — thin wrapper over modules/env_stack.
# Identical across dev/qa/prod/ephemeral; only variable defaults differ.
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
  backend "s3" {} # partial — filled by fabfile via -backend-config (bootstrap first)
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
