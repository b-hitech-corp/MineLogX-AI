# Lambda Layer built from a pre-installed `python/` directory tree.
#
# The directory itself is produced OUTSIDE Terraform by `fab lambda.build-layer
# <fn>` (pip install --platform manylinux... --target), because Terraform has
# no native "pip install" primitive. This module only zips + publishes it, so
# `terraform plan` stays deterministic and `source_code_hash` tracks real
# content changes (rebuild the dir, then re-plan/apply).

terraform {
  required_providers {
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

variable "layer_name" {
  type = string
}

variable "build_dir" {
  description = "Directory containing the layer root (must have a `python/` subdir) — produced by `fab lambda.build-layer`."
  type        = string
}

variable "compatible_runtimes" {
  type    = list(string)
  default = ["python3.11"]
}

data "archive_file" "layer" {
  type        = "zip"
  source_dir  = var.build_dir
  output_path = "${path.module}/.build/${var.layer_name}.zip"
}

resource "aws_lambda_layer_version" "this" {
  layer_name          = var.layer_name
  filename            = data.archive_file.layer.output_path
  source_code_hash    = data.archive_file.layer.output_base64sha256
  compatible_runtimes = var.compatible_runtimes
}

output "arn" {
  value = aws_lambda_layer_version.this.arn
}
