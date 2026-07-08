# Step Functions state machine for the CSV Vectorization Pipeline.
# Orchestrates the pipeline stages by invoking the CSV Lambda sequentially
# (Stage 1 schema → Stages 2-3 normalize+chunk → Stage 4 OpenSearch ingest),
# matching the diagram: EventBridge Scheduler → Step Functions → Lambda → Bedrock.

variable "name_prefix" {
  type = string
}

variable "csv_lambda_arn" {
  description = "ARN of the CSV pipeline Lambda invoked by each stage."
  type        = string
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
      identifiers = ["states.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "invoke" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [var.csv_lambda_arn, "${var.csv_lambda_arn}:*"]
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.name_prefix}-csv-sfn-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = merge(var.tags, { Name = "${var.name_prefix}-csv-sfn-role" })
}

resource "aws_iam_role_policy" "invoke" {
  name   = "invoke-csv-lambda"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.invoke.json
}

resource "aws_sfn_state_machine" "csv" {
  name     = "${var.name_prefix}-csv-pipeline"
  role_arn = aws_iam_role.sfn.arn
  tags     = merge(var.tags, { Name = "${var.name_prefix}-csv-pipeline" })

  # Each state passes the input file_path through and selects which pipeline
  # stages the Lambda runs. The Lambda is idempotent per stage (S3 artifact check).
  definition = jsonencode({
    Comment = "MineLogX CSV vectorization pipeline"
    StartAt = "SchemaInspection"
    States = {
      SchemaInspection = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = var.csv_lambda_arn
          Payload = {
            "file_path.$" = "$.file_path"
            "force.$"     = "$.force"
            stages        = [1]
          }
        }
        ResultPath = "$.stage1"
        Next       = "NormalizeAndChunk"
      }
      NormalizeAndChunk = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = var.csv_lambda_arn
          Payload = {
            "file_path.$" = "$.file_path"
            "force.$"     = "$.force"
            stages        = [2, 3]
          }
        }
        ResultPath = "$.stage23"
        Next       = "OpenSearchIngest"
      }
      OpenSearchIngest = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = var.csv_lambda_arn
          Payload = {
            "file_path.$" = "$.file_path"
            "force.$"     = "$.force"
            stages        = [4]
          }
        }
        ResultPath = "$.stage4"
        End        = true
      }
    }
  })
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.csv.arn
}

output "state_machine_name" {
  value = aws_sfn_state_machine.csv.name
}
