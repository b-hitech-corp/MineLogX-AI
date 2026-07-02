# Project S3 buckets — modeled on the demo (telemetry + legislation data lakes).
# Names are prefixed per environment for global uniqueness.

variable "name_prefix" {
  type = string
}

variable "buckets" {
  description = "Logical suffix => bucket settings."
  type = map(object({
    versioning = bool
  }))
  default = {
    "telemetry-data"        = { versioning = false }
    "legislation-documents" = { versioning = false }
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_s3_bucket" "this" {
  for_each = var.buckets
  bucket   = "${var.name_prefix}-${each.key}"
  tags     = merge(var.tags, { Name = "${var.name_prefix}-${each.key}" })
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = { for k, v in var.buckets : k => v if v.versioning }
  bucket   = aws_s3_bucket.this[each.key].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "bucket_ids" {
  value = { for k, v in aws_s3_bucket.this : k => v.id }
}

output "bucket_arns" {
  value = { for k, v in aws_s3_bucket.this : k => v.arn }
}
