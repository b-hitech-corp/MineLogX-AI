# EventBridge triggers for the two vectorization pipelines:
#   - CSV batch: an EventBridge Scheduler fires the CSV Step Functions on a cron.
#   - PDF event-driven: an EventBridge Rule on S3 "Object Created" invokes the
#     PDF Lambda when a .pdf lands in the legislation bucket.
# The bucket must have EventBridge notifications enabled (see the s3 module).

variable "name_prefix" {
  type = string
}

variable "state_machine_arn" {
  description = "CSV pipeline Step Functions ARN targeted by the scheduler."
  type        = string
}

variable "schedule_expression" {
  description = "EventBridge Scheduler expression for the CSV batch run."
  type        = string
  default     = "rate(1 day)"
}

variable "pdf_lambda_arn" {
  type = string
}

variable "pdf_lambda_name" {
  type = string
}

variable "pdf_bucket_name" {
  description = "Bucket whose .pdf uploads trigger the PDF Lambda."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

# --------------------------------------------------------------------------- #
# CSV batch — EventBridge Scheduler -> Step Functions
# --------------------------------------------------------------------------- #
data "aws_iam_policy_document" "sched_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "sched_start" {
  statement {
    actions   = ["states:StartExecution"]
    resources = [var.state_machine_arn]
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name_prefix}-csv-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.sched_assume.json
  tags               = merge(var.tags, { Name = "${var.name_prefix}-csv-scheduler-role" })
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "start-csv-sfn"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.sched_start.json
}

resource "aws_scheduler_schedule" "csv_batch" {
  name = "${var.name_prefix}-csv-batch"
  flexible_time_window {
    mode = "OFF"
  }
  schedule_expression = var.schedule_expression
  target {
    arn      = var.state_machine_arn
    role_arn = aws_iam_role.scheduler.arn
  }
}

# --------------------------------------------------------------------------- #
# PDF event-driven — S3 Object Created (EventBridge Rule) -> Lambda
# --------------------------------------------------------------------------- #
resource "aws_cloudwatch_event_rule" "pdf_upload" {
  name        = "${var.name_prefix}-pdf-upload"
  description = "PDF upload to the legislation bucket triggers the PDF pipeline."
  tags        = merge(var.tags, { Name = "${var.name_prefix}-pdf-upload" })
  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [var.pdf_bucket_name] }
      object = { key = [{ suffix = ".pdf" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "pdf_lambda" {
  rule      = aws_cloudwatch_event_rule.pdf_upload.name
  target_id = "pdf-lambda"
  arn       = var.pdf_lambda_arn
}

resource "aws_lambda_permission" "pdf_from_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = var.pdf_lambda_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.pdf_upload.arn
}

output "csv_schedule_name" {
  value = aws_scheduler_schedule.csv_batch.name
}

output "pdf_rule_arn" {
  value = aws_cloudwatch_event_rule.pdf_upload.arn
}
