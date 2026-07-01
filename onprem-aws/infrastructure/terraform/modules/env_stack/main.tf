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

variable "tags" {
  type    = map(string)
  default = {}
}

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
  source      = "../s3"
  name_prefix = var.name_prefix
  tags        = var.tags
}

module "iam" {
  source      = "../iam"
  name_prefix = var.name_prefix
  tags        = var.tags
}

module "cloudwatch" {
  source       = "../cloudwatch"
  lambda_names = ["${var.name_prefix}-ml", "${var.name_prefix}-rag"]
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

output "vpc_id" {
  value = module.network.vpc_id
}

output "api_execution_arn" {
  value = module.api_gateway.execution_arn
}

output "amplify_default_domain" {
  value = module.amplify.default_domain
}
