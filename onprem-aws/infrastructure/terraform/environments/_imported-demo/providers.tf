terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# Minimal provider (no default_tags) so the import stays faithful — we want
# `terraform plan` to report 0 changes after adopting the demo, not try to add tags.
provider "aws" {
  region = "us-east-1"
}
