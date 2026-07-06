# Generic Lambda function — packages a source directory into a zip and wires it
# to an execution role + log group. Reused for the API, CSV and PDF functions.
#
# THIN packaging: archive_file zips the handler package only. Heavy runtime deps
# (pandas, pyarrow, pdfplumber, opensearch-py, strands) are NOT installed here —
# attach them via `layer_arns` (a deps layer / container image built separately).
# The function is deployable and updatable; it only RUNS once deps are provided.

terraform {
  required_providers {
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

variable "function_name" {
  type = string
}

variable "handler" {
  description = "Entrypoint, e.g. pdf_pipeline.agent.pdf_vectorization_pipeline.lambda_handler."
  type        = string
}

variable "runtime" {
  type    = string
  default = "python3.11"
}

variable "source_dir" {
  description = "Directory zipped into the deployment package."
  type        = string
}

variable "excludes" {
  description = "Glob paths (relative to source_dir) kept out of the zip."
  type        = list(string)
  default     = []
}

variable "role_arn" {
  type = string
}

variable "timeout" {
  type    = number
  default = 60
}

variable "memory_size" {
  type    = number
  default = 512
}

variable "environment" {
  description = "Environment variables passed to the function."
  type        = map(string)
  default     = {}
}

variable "layer_arns" {
  description = "Lambda layer ARNs providing runtime dependencies."
  type        = list(string)
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}

data "archive_file" "pkg" {
  type        = "zip"
  source_dir  = var.source_dir
  excludes    = var.excludes
  output_path = "${path.module}/.build/${var.function_name}.zip"
}

resource "aws_lambda_function" "this" {
  function_name    = var.function_name
  handler          = var.handler
  runtime          = var.runtime
  role             = var.role_arn
  filename         = data.archive_file.pkg.output_path
  source_code_hash = data.archive_file.pkg.output_base64sha256
  timeout          = var.timeout
  memory_size      = var.memory_size
  layers           = var.layer_arns

  dynamic "environment" {
    for_each = length(var.environment) > 0 ? [1] : []
    content {
      variables = var.environment
    }
  }

  tags = merge(var.tags, { Name = var.function_name })
}

output "function_name" {
  value = aws_lambda_function.this.function_name
}

output "function_arn" {
  value = aws_lambda_function.this.arn
}

output "invoke_arn" {
  value = aws_lambda_function.this.invoke_arn
}
