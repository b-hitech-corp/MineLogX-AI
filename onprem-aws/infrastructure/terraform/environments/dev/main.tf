terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # backend "s3" configured per environment — see ../../backend.tf.
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      "aws-apn-id" = var.project_apn_id
      Project      = "MineLogX-AI"
      Environment  = "dev"
      ManagedBy    = "terraform"
    }
  }
}

locals {
  name_prefix = "minelogx-dev"
  tags        = {}
}

module "network" {
  source      = "../../modules/network"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "security_groups" {
  source            = "../../modules/security_groups"
  name_prefix       = local.name_prefix
  vpc_id            = module.network.vpc_id
  ssh_ingress_cidrs = var.ssh_ingress_cidrs
  tags              = local.tags
}

# EC2 Ollama fallback tier — off by default in dev (Bedrock is primary).
module "ec2_llm" {
  source             = "../../modules/ec2_llm"
  enabled            = var.enable_llm_fallback
  name_prefix        = local.name_prefix
  subnet_id          = module.network.private_subnet_ids[0]
  security_group_ids = [module.security_groups.sg_llm_id]
  key_name           = var.ec2_key_name
  tags               = local.tags
}

module "s3" {
  source      = "../../modules/s3"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "iam" {
  source      = "../../modules/iam"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "cloudwatch" {
  source       = "../../modules/cloudwatch"
  lambda_names = ["${local.name_prefix}-ml", "${local.name_prefix}-rag"]
  tags         = local.tags
}

module "api_gateway" {
  source      = "../../modules/api_gateway"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "amplify" {
  source      = "../../modules/amplify"
  name_prefix = local.name_prefix
  tags        = local.tags
}
