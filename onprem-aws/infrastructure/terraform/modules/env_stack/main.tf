# Environment composition — wires all base modules into a full MineLogX stack.
# Consumed by each environment root (dev/qa/prod/ephemeral); the only per-env
# differences are variable values, so the wiring lives here once (DRY).

variable "name_prefix" {
  description = "Resource name prefix, e.g. minelogx-dev / minelogx-dev-cesar."
  type        = string
}

variable "enable_llm_fallback" {
  description = "Stand up the EC2 Ollama fallback tier (backup when Bedrock is down)."
  type        = bool
  default     = false
}

variable "ssh_ingress_cidrs" {
  type    = list(string)
  default = []
}

variable "ec2_key_name" {
  type    = string
  default = null
}

variable "csv_schedule_expression" {
  description = "EventBridge Scheduler expression for the CSV batch pipeline."
  type        = string
  default     = "rate(1 day)"
}

variable "build_csv_layer" {
  description = "Publish the CSV deps layer (built by `fab lambda.build-layer csv` into csv_layer_build_dir). Off by default so `terraform plan` doesn't fail before the layer has been built once."
  type        = bool
  default     = false
}

variable "csv_layer_build_dir" {
  description = "Directory containing the CSV layer's `python/` tree, produced by `fab lambda.build-layer csv`."
  type        = string
  default     = null
}

variable "build_pdf_layer" {
  description = "Publish the PDF deps layer (built by `fab lambda.build-layer pdf` into pdf_layer_build_dir). Off by default so `terraform plan` doesn't fail before the layer has been built once."
  type        = bool
  default     = false
}

variable "pdf_layer_build_dir" {
  description = "Directory containing the PDF layer's `python/` tree, produced by `fab lambda.build-layer pdf`."
  type        = string
  default     = null
}

variable "tags" {
  type    = map(string)
  default = {}
}

data "aws_caller_identity" "current" {}

locals {
  # backend/ lives at onprem-aws/backend; this module is at
  # onprem-aws/infrastructure/terraform/modules/env_stack.
  backend_dir = "${path.module}/../../../../backend"

  # Keep tests/notebooks/assets out of the deployment package (thin zip).
  lambda_excludes = [
    "**/tests/**",
    "**/__pycache__/**",
    "**/*.ipynb",
    "**/*.png",
    "**/*.md",
    "data_analysis_agent/data_output*.json",
    "sample_data/**",
    "lambdas/**",
  ]

  telemetry_bucket   = module.s3.bucket_ids["telemetry-data"]
  legislation_bucket = module.s3.bucket_ids["legislation-documents"]

  pdf_layer_build_dir = coalesce(var.pdf_layer_build_dir, "${local.backend_dir}/.layers/pdf")
  csv_layer_build_dir = coalesce(var.csv_layer_build_dir, "${local.backend_dir}/.layers/csv")

  # Bedrock InvokeModel — foundation models + cross-region inference profiles.
  bedrock_model_arns = [
    "arn:aws:bedrock:*::foundation-model/*",
    "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/*",
  ]

  fn = {
    api = "${var.name_prefix}-api"
    csv = "${var.name_prefix}-csv"
    pdf = "${var.name_prefix}-pdf"
  }
}

# --------------------------------------------------------------------------- #
# Base infrastructure
# --------------------------------------------------------------------------- #
module "network" {
  source      = "../network"
  name_prefix = var.name_prefix
  tags        = var.tags
}

module "security_groups" {
  source            = "../security_groups"
  name_prefix       = var.name_prefix
  vpc_id            = module.network.vpc_id
  ssh_ingress_cidrs = var.ssh_ingress_cidrs
  tags              = var.tags
}

module "ec2_llm" {
  source             = "../ec2_llm"
  enabled            = var.enable_llm_fallback
  name_prefix        = var.name_prefix
  subnet_id          = module.network.private_subnet_ids[0]
  security_group_ids = [module.security_groups.sg_llm_id]
  key_name           = var.ec2_key_name
  tags               = var.tags
}

module "s3" {
  source              = "../s3"
  name_prefix         = var.name_prefix
  eventbridge_buckets = ["legislation-documents"] # PDF pipeline trigger source
  tags                = var.tags
}

module "iam" {
  source      = "../iam"
  name_prefix = var.name_prefix
  roles = {
    api = []
    csv = []
    pdf = []
  }
  tags = var.tags
}

module "cloudwatch" {
  source       = "../cloudwatch"
  lambda_names = [local.fn.api, local.fn.csv, local.fn.pdf]
  tags         = var.tags
}

module "api_gateway" {
  source      = "../api_gateway"
  name_prefix = var.name_prefix
  tags        = var.tags
}

module "amplify" {
  source      = "../amplify"
  name_prefix = var.name_prefix
  tags        = var.tags
}

# --------------------------------------------------------------------------- #
# AI layer — vector store + guardrail
# --------------------------------------------------------------------------- #
module "opensearch" {
  source      = "../opensearch"
  name_prefix = var.name_prefix
  tags        = var.tags
}

module "bedrock_guardrails" {
  source      = "../bedrock_guardrails"
  name_prefix = var.name_prefix
  tags        = var.tags
}

# --------------------------------------------------------------------------- #
# Lambda execution role policies (bedrock / aoss / s3 / textract)
# --------------------------------------------------------------------------- #
data "aws_iam_policy_document" "bedrock" {
  statement {
    sid       = "InvokeModels"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = local.bedrock_model_arns
  }
  statement {
    sid       = "ApplyGuardrail"
    actions   = ["bedrock:ApplyGuardrail"]
    resources = [module.bedrock_guardrails.guardrail_arn]
  }
}

data "aws_iam_policy_document" "aoss" {
  statement {
    actions   = ["aoss:APIAccessAll"]
    resources = [module.opensearch.collection_arn]
  }
}

# API role — read-only S3, Bedrock invoke, AOSS query. The CSV pipeline is
# triggered by the EventBridge Scheduler (per the diagram), not by the API.
data "aws_iam_policy_document" "api" {
  source_policy_documents = [data.aws_iam_policy_document.bedrock.json, data.aws_iam_policy_document.aoss.json]
  statement {
    sid       = "S3Read"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [module.s3.bucket_arns["telemetry-data"], "${module.s3.bucket_arns["telemetry-data"]}/*", module.s3.bucket_arns["legislation-documents"], "${module.s3.bucket_arns["legislation-documents"]}/*"]
  }
}

# CSV role — read/write telemetry bucket, Bedrock (Claude + Cohere), AOSS ingest.
data "aws_iam_policy_document" "csv" {
  source_policy_documents = [data.aws_iam_policy_document.bedrock.json, data.aws_iam_policy_document.aoss.json]
  statement {
    sid       = "S3ReadWrite"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [module.s3.bucket_arns["telemetry-data"], "${module.s3.bucket_arns["telemetry-data"]}/*"]
  }
}

# PDF role — read/write legislation bucket, Textract, Bedrock (Claude + Titan), AOSS ingest.
data "aws_iam_policy_document" "pdf" {
  source_policy_documents = [data.aws_iam_policy_document.bedrock.json, data.aws_iam_policy_document.aoss.json]
  statement {
    sid       = "S3ReadWrite"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [module.s3.bucket_arns["legislation-documents"], "${module.s3.bucket_arns["legislation-documents"]}/*"]
  }
  statement {
    sid       = "Textract"
    actions   = ["textract:AnalyzeDocument", "textract:DetectDocumentText", "textract:StartDocumentAnalysis", "textract:GetDocumentAnalysis"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "api-access"
  role   = module.iam.role_names["api"]
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_iam_role_policy" "csv" {
  name   = "csv-access"
  role   = module.iam.role_names["csv"]
  policy = data.aws_iam_policy_document.csv.json
}

resource "aws_iam_role_policy" "pdf" {
  name   = "pdf-access"
  role   = module.iam.role_names["pdf"]
  policy = data.aws_iam_policy_document.pdf.json
}

# --------------------------------------------------------------------------- #
# Lambda functions (thin handlers over backend/ code)
# --------------------------------------------------------------------------- #
module "lambda_api" {
  source        = "../lambda"
  function_name = local.fn.api
  handler       = "handler.lambda_handler"
  source_dir    = "${local.backend_dir}/lambdas/api"
  role_arn      = module.iam.role_arns["api"]
  timeout       = 30
  memory_size   = 512
  environment = {
    OPENSEARCH_HOST = module.opensearch.collection_host
    GUARDRAIL_ID    = module.bedrock_guardrails.guardrail_id
  }
  tags = var.tags
}

module "lambda_layer_csv" {
  count      = var.build_csv_layer ? 1 : 0
  source     = "../lambda_layer"
  layer_name = "${var.name_prefix}-csv-deps"
  build_dir  = local.csv_layer_build_dir
}

module "lambda_csv" {
  source        = "../lambda"
  function_name = local.fn.csv
  handler       = "csv_pipeline.lambda_function.lambda_handler"
  source_dir    = local.backend_dir
  excludes      = local.lambda_excludes
  role_arn      = module.iam.role_arns["csv"]
  timeout       = 300
  memory_size   = 1024
  layer_arns    = var.build_csv_layer ? [module.lambda_layer_csv[0].arn] : []
  environment = {
    OPENSEARCH_HOST  = module.opensearch.collection_host
    OPENSEARCH_INDEX = "csv_telemetry_vecs"
    FLEET_S3_BUCKET  = local.telemetry_bucket
    FLEET_S3_PREFIX  = ""
    GUARDRAIL_ID     = module.bedrock_guardrails.guardrail_id
  }
  tags = var.tags
}

module "lambda_layer_pdf" {
  count      = var.build_pdf_layer ? 1 : 0
  source     = "../lambda_layer"
  layer_name = "${var.name_prefix}-pdf-deps"
  build_dir  = local.pdf_layer_build_dir
}

module "lambda_pdf" {
  source        = "../lambda"
  function_name = local.fn.pdf
  handler       = "pdf_pipeline.agent.pdf_vectorization_pipeline.lambda_handler"
  source_dir    = local.backend_dir
  excludes      = local.lambda_excludes
  role_arn      = module.iam.role_arns["pdf"]
  timeout       = 300
  memory_size   = 1024
  layer_arns    = var.build_pdf_layer ? [module.lambda_layer_pdf[0].arn] : []
  environment = {
    OPENSEARCH_HOST      = module.opensearch.collection_host
    PDF_OPENSEARCH_INDEX = "pdf_legal_vecs"
    PDF_ARTIFACT_BUCKET  = local.legislation_bucket
    GUARDRAIL_ID         = module.bedrock_guardrails.guardrail_id
  }
  tags = var.tags
}

# --------------------------------------------------------------------------- #
# Orchestration — Step Functions (CSV) + EventBridge (scheduler + PDF rule)
# --------------------------------------------------------------------------- #
module "step_functions" {
  source         = "../step_functions"
  name_prefix    = var.name_prefix
  csv_lambda_arn = module.lambda_csv.function_arn
  tags           = var.tags
}

module "eventbridge" {
  source              = "../eventbridge"
  name_prefix         = var.name_prefix
  state_machine_arn   = module.step_functions.state_machine_arn
  schedule_expression = var.csv_schedule_expression
  pdf_lambda_arn      = module.lambda_pdf.function_arn
  pdf_lambda_name     = module.lambda_pdf.function_name
  pdf_bucket_name     = local.legislation_bucket
  tags                = var.tags
}

# --------------------------------------------------------------------------- #
# Outputs
# --------------------------------------------------------------------------- #
output "vpc_id" {
  value = module.network.vpc_id
}

output "api_execution_arn" {
  value = module.api_gateway.execution_arn
}

output "amplify_default_domain" {
  value = module.amplify.default_domain
}

output "opensearch_endpoint" {
  value = module.opensearch.collection_endpoint
}

output "guardrail_id" {
  value = module.bedrock_guardrails.guardrail_id
}

output "csv_state_machine_arn" {
  value = module.step_functions.state_machine_arn
}

output "lambda_function_names" {
  value = [module.lambda_api.function_name, module.lambda_csv.function_name, module.lambda_pdf.function_name]
}
