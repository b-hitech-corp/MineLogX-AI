variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_apn_id" {
  type    = string
  default = "pc:925kllxsozl58ehxuk1rxxd8z" # PROD uses a distinct APN id
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "name_prefix" {
  type    = string
  default = "minelogx-prod"
}

variable "enable_llm_fallback" {
  description = "EC2 Ollama fallback tier. ON in prod — backup when Bedrock is down."
  type        = bool
  default     = true
}

variable "ssh_ingress_cidrs" {
  type    = list(string)
  default = []
}

variable "ec2_key_name" {
  type    = string
  default = null
}
