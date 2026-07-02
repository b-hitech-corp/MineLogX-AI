# REST API Gateway fronting the Lambdas — modeled on minelogx-api-demo-poc.
# Routes/integrations are wired per environment (kept minimal here on purpose).

variable "name_prefix" {
  type = string
}

variable "description" {
  type    = string
  default = "MineLogX API"
}

variable "endpoint_type" {
  type    = string
  default = "REGIONAL"
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_api_gateway_rest_api" "this" {
  name        = "${var.name_prefix}-api"
  description = var.description
  endpoint_configuration {
    types = [var.endpoint_type]
  }
  tags = var.tags
}

output "rest_api_id" {
  value = aws_api_gateway_rest_api.this.id
}

output "root_resource_id" {
  value = aws_api_gateway_rest_api.this.root_resource_id
}

output "execution_arn" {
  value = aws_api_gateway_rest_api.this.execution_arn
}
