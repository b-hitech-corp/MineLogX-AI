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
  default = "qa"
}

variable "name_prefix" {
  type    = string
  default = "minelogx-qa"
}

variable "enable_llm_fallback" {
  description = "EC2 Ollama fallback tier. Off in qa."
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

variable "build_pdf_layer" {
  description = "Publish the PDF deps Lambda layer (run `fab lambda.build-layer pdf` first)."
  type        = bool
  default     = false
}

variable "build_csv_layer" {
  description = "Publish the CSV deps Lambda layer (run `fab lambda.build-layer csv` first)."
  type        = bool
  default     = false
}
