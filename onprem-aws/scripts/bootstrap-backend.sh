#!/usr/bin/env bash
#
# bootstrap-backend.sh — one-time creation of the Terraform remote-state backend
# (S3 bucket + DynamoDB lock table) for the onprem-aws target.
#
# Run ONCE per account before the first `fab env.up`. Idempotent-ish: it errors
# if the bucket/table already exist (safe to ignore).
#
# Usage:
#   AWS_PROFILE=minelogx-admin bash onprem-aws/scripts/bootstrap-backend.sh
#
set -euo pipefail

REGION="${REGION:-us-east-1}"
BUCKET="${BUCKET:-minelogx-terraform-state}"
TABLE="${TABLE:-minelogx-terraform-locks}"
TAG="aws-apn-id=pc:13uw3s8iyvze74tlcq3o0w8r6"

echo "== Bootstrapping Terraform backend =="
echo "  Bucket: $BUCKET   Table: $TABLE   Region: $REGION"

# S3 state bucket (us-east-1 takes no LocationConstraint).
aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-tagging --bucket "$BUCKET" \
  --tagging "TagSet=[{Key=aws-apn-id,Value=pc:13uw3s8iyvze74tlcq3o0w8r6}]"

# DynamoDB lock table.
aws dynamodb create-table --table-name "$TABLE" --region "$REGION" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --tags Key=aws-apn-id,Value=pc:13uw3s8iyvze74tlcq3o0w8r6

echo "Done. State keys will be: onprem-aws/<env>/terraform.tfstate"
