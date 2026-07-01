terraform {
  # Import blocks + `-generate-config-out` require Terraform >= 1.5.
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    # awscc is used for resources with immature aws-provider support
    # (e.g. some Bedrock / OpenSearch Serverless resources) when the target
    # architecture is implemented in Terraform.
    awscc = {
      source  = "hashicorp/awscc"
      version = "~> 1.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      "aws-apn-id" = var.project_apn_id
      "Project"    = "MineLogX-AI"
      "ManagedBy"  = "terraform"
      "Environment" = var.environment
    }
  }
}
