# Shared input variables for MineLogX-AI Terraform.
# Root modules under environments/ reference these via a symlink or module call.

variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "project_apn_id" {
  description = "Value of the aws-apn-id tag used to identify project resources."
  type        = string
  default     = "pc:13uw3s8iyvze74tlcq3o0w8r6"
}

variable "environment" {
  description = "Environment name: dev | qa | prod | dev-<user> (ephemeral)."
  type        = string
}

variable "name_prefix" {
  description = "Resource name prefix. Convention: minelogx-<environment>."
  type        = string
  default     = "minelogx"
}
