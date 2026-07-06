# Lambda execution roles — modeled on the demo service roles (ml, rag).
# One role per logical Lambda, with basic execution logging + optional extra
# managed/inline policies (S3 read, Bedrock invoke, etc.) passed per environment.

variable "name_prefix" {
  type = string
}

variable "roles" {
  description = "Logical Lambda name => extra managed policy ARNs to attach."
  type        = map(list(string))
  default = {
    ml  = []
    rag = []
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  for_each           = var.roles
  name               = "${var.name_prefix}-lambda-${each.key}-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = merge(var.tags, { Name = "${var.name_prefix}-lambda-${each.key}-role" })
}

# Basic execution (CloudWatch Logs) for every role.
resource "aws_iam_role_policy_attachment" "basic" {
  for_each   = aws_iam_role.lambda
  role       = each.value.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Extra per-role managed policies (flattened to role/policy pairs).
resource "aws_iam_role_policy_attachment" "extra" {
  for_each = { for pair in flatten([
    for role, arns in var.roles : [for arn in arns : { role = role, arn = arn }]
  ]) : "${pair.role}-${pair.arn}" => pair }
  role       = aws_iam_role.lambda[each.value.role].name
  policy_arn = each.value.arn
}

output "role_arns" {
  value = { for k, v in aws_iam_role.lambda : k => v.arn }
}

output "role_names" {
  value = { for k, v in aws_iam_role.lambda : k => v.name }
}
