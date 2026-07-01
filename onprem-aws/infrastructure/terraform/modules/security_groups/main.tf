# Security group for the LLM (Ollama) fallback instances — modeled on the
# imported minelogx-sg-llm.

variable "name_prefix" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "ollama_port" {
  description = "Ollama model-serving port."
  type        = number
  default     = 11434
}

variable "extra_tcp_ports" {
  description = "Additional TCP ports to open to the ingress CIDRs (e.g. app ports)."
  type        = list(number)
  default     = [8000]
}

variable "app_ingress_cidrs" {
  description = "CIDRs allowed to reach the model/app ports."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "ssh_ingress_cidrs" {
  description = "CIDRs allowed SSH (22). Keep tight — operator IPs only."
  type        = list(string)
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_security_group" "llm" {
  name        = "${var.name_prefix}-sg-llm"
  description = "Security group for MineLogX LLM instances"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${var.name_prefix}-sg-llm" })
}

resource "aws_vpc_security_group_ingress_rule" "model" {
  security_group_id = aws_security_group.llm.id
  for_each          = toset(var.app_ingress_cidrs)
  cidr_ipv4         = each.value
  from_port         = var.ollama_port
  to_port           = var.ollama_port
  ip_protocol       = "tcp"
  description       = "Ollama endpoint"
}

resource "aws_vpc_security_group_ingress_rule" "extra" {
  for_each          = { for pair in setproduct(var.extra_tcp_ports, var.app_ingress_cidrs) : "${pair[0]}-${pair[1]}" => { port = pair[0], cidr = pair[1] } }
  security_group_id = aws_security_group.llm.id
  cidr_ipv4         = each.value.cidr
  from_port         = each.value.port
  to_port           = each.value.port
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  for_each          = toset(var.ssh_ingress_cidrs)
  security_group_id = aws_security_group.llm.id
  cidr_ipv4         = each.value
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
  description       = "SSH"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.llm.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

output "sg_llm_id" {
  value = aws_security_group.llm.id
}
