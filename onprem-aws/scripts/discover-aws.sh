#!/usr/bin/env bash
#
# discover-aws.sh — Read-only inventory of the MineLogX-AI POC deployed in AWS.
#
# Dumps the current state of the account (filtered by the project tag) into
# infrastructure/discovery/ so it can be reverse-engineered into IaC.
#
# This script performs NO mutations. It only calls list-*/describe-*/get-*.
#
# Prerequisites:
#   - AWS CLI v2 configured (`aws configure` or an SSO profile).
#   - Permissions to read the services below.
#
# Usage:
#   AWS_PROFILE=minelogx ./scripts/discover-aws.sh
#   REGION=us-east-1 ./scripts/discover-aws.sh
#
set -euo pipefail

# ---- Config -----------------------------------------------------------------
PROJECT_TAG_KEY="aws-apn-id"
PROJECT_TAG_VALUE="pc:13uw3s8iyvze74tlcq3o0w8r6"
REGION="${REGION:-us-east-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${OUT:-$SCRIPT_DIR/../infrastructure/discovery}"

TAG_FILTER="Key=${PROJECT_TAG_KEY},Values=${PROJECT_TAG_VALUE}"

mkdir -p "$OUT"

# Helper: run a command, save stdout to a file, never abort the whole script
# if a single service is unavailable/denied (some APIs may not be enabled).
dump() {
  local file="$1"; shift
  echo "  -> $file"
  if ! "$@" > "$OUT/$file" 2> "$OUT/$file.err"; then
    echo "     (warning: command failed — see $OUT/$file.err)"
  else
    rm -f "$OUT/$file.err"
  fi
}

echo "== MineLogX-AI AWS discovery =="
echo "Region : $REGION"
echo "Tag    : ${PROJECT_TAG_KEY}=${PROJECT_TAG_VALUE}"
echo "Output : $OUT"
echo

# ---- Identity ---------------------------------------------------------------
echo "[identity]"
dump identity.json                aws sts get-caller-identity

# ---- Tag-based inventory ----------------------------------------------------
echo "[tag inventory]"
dump tagged-resources.json        aws resourcegroupstaggingapi get-resources --tag-filters "$TAG_FILTER" --region "$REGION"
# Resource Explorer is multi-region (requires an aggregator index to be enabled).
dump resource-explorer.json       aws resource-explorer-2 search --query-string "tag:${PROJECT_TAG_KEY}=${PROJECT_TAG_VALUE}" --region "$REGION"

# ---- Networking -------------------------------------------------------------
echo "[networking]"
dump vpcs.json                    aws ec2 describe-vpcs --region "$REGION"
dump subnets.json                 aws ec2 describe-subnets --region "$REGION"
dump security-groups.json         aws ec2 describe-security-groups --region "$REGION"
dump route-tables.json            aws ec2 describe-route-tables --region "$REGION"
dump internet-gateways.json       aws ec2 describe-internet-gateways --region "$REGION"
dump nat-gateways.json            aws ec2 describe-nat-gateways --region "$REGION"

# ---- Compute (EC2 Ollama, POC) ----------------------------------------------
echo "[compute]"
dump ec2-instances.json           aws ec2 describe-instances --region "$REGION"
dump key-pairs.json               aws ec2 describe-key-pairs --region "$REGION"

# ---- Lambda -----------------------------------------------------------------
echo "[lambda]"
dump lambdas.json                 aws lambda list-functions --region "$REGION"

# ---- API Gateway ------------------------------------------------------------
echo "[api gateway]"
dump apigw-rest.json              aws apigateway get-rest-apis --region "$REGION"
dump apigw-http.json              aws apigatewayv2 get-apis --region "$REGION"

# ---- Storage ----------------------------------------------------------------
echo "[s3]"
dump s3-buckets.json              aws s3api list-buckets

# ---- IAM --------------------------------------------------------------------
echo "[iam]"
dump iam-roles.json               aws iam list-roles
dump iam-policies.json            aws iam list-policies --scope Local

# ---- Eventing / orchestration ----------------------------------------------
echo "[eventbridge / step functions]"
dump eventbridge-rules.json       aws events list-rules --region "$REGION"
dump eventbridge-schedulers.json  aws scheduler list-schedules --region "$REGION"
dump stepfunctions.json           aws stepfunctions list-state-machines --region "$REGION"

# ---- AI / search ------------------------------------------------------------
echo "[opensearch / bedrock]"
dump opensearch-serverless.json   aws opensearchserverless list-collections --region "$REGION"
dump opensearch-domains.json      aws opensearch list-domain-names --region "$REGION"
dump bedrock-guardrails.json      aws bedrock list-guardrails --region "$REGION"

# ---- Observability ----------------------------------------------------------
echo "[cloudwatch logs]"
dump log-groups.json              aws logs describe-log-groups --region "$REGION"

echo
echo "Done. Inventory written to $OUT/"
echo "Share the contents of $OUT/ (it is gitignored) to generate the IaC."
