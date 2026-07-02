variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_apn_id" {
  type    = string
  default = "pc:13uw3s8iyvze74tlcq3o0w8r6"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "name_prefix" {
  type    = string
  default = "minelogx-dev"
}

variable "enable_llm_fallback" {
  description = "EC2 Ollama fallback tier. Off in dev (Bedrock is primary)."
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
