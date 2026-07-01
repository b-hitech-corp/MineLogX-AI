variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_apn_id" {
  type    = string
  default = "pc:13uw3s8iyvze74tlcq3o0w8r6"
}

variable "ssh_ingress_cidrs" {
  description = "Operator CIDRs allowed SSH to the LLM fallback instances."
  type        = list(string)
  default     = []
}

variable "ec2_key_name" {
  type    = string
  default = null
}

variable "enable_llm_fallback" {
  description = "Stand up the EC2 Ollama fallback tier in this environment."
  type        = bool
  default     = false
}
