# CloudWatch log groups for the Lambdas — modeled on /aws/lambda/minelogx-* .

variable "lambda_names" {
  description = "Full Lambda function names (log group is /aws/lambda/<name>)."
  type        = list(string)
}

variable "retention_in_days" {
  description = "Log retention. 0 = never expire (demo default)."
  type        = number
  default     = 30
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = toset(var.lambda_names)
  name              = "/aws/lambda/${each.value}"
  retention_in_days = var.retention_in_days
  tags              = var.tags
}

output "log_group_names" {
  value = [for lg in aws_cloudwatch_log_group.lambda : lg.name]
}
