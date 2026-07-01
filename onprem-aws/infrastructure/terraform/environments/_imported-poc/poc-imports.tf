# POC import blocks — account 586928288932, us-east-1.
# Source of truth: onprem-aws/infrastructure/discovery/tagged-resources.json (tag aws-apn-id).
#
# Workflow:
#   cd onprem-aws/infrastructure/terraform/environments/_imported-poc
#   terraform init
#   terraform plan -generate-config-out=generated.tf   # generates HCL for the below
#   # review generated.tf, then refactor into ../../modules/*
#   terraform apply                                     # adopt state (imports only)
#   terraform plan                                      # must report 0 changes
#
# generated.tf is scratch — do not commit it as-is; fold it into modules.

# --- Networking ---
import {
  to = aws_vpc.main
  id = "vpc-0a7b98533f5eaa246"
}
import {
  to = aws_security_group.sg_02bfd8f5
  id = "sg-02bfd8f53ea36cab6"
}
import {
  to = aws_security_group.sg_0ecd06f9
  id = "sg-0ecd06f99c79f13ed"
}
import {
  to = aws_security_group.sg_09b9fd36
  id = "sg-09b9fd36d49de12f7"
}
import {
  to = aws_security_group.sg_072b0506
  id = "sg-072b050667fa07443"
}

# --- Compute: Ollama POC EC2 instances ---
import {
  to = aws_instance.qwen3
  id = "i-04ae7cc1172b65ca9"
}
import {
  to = aws_instance.gemma3
  id = "i-0cb3ce327041effa6"
}
import {
  to = aws_instance.embeddings
  id = "i-00cb0a577f2fc6d7b"
}

# --- Storage: S3 buckets ---
import {
  to = aws_s3_bucket.telemetry_data
  id = "bhitech-minelogx-poc-telemetry-data"
}
import {
  to = aws_s3_bucket.legislation_documents
  id = "bhitech-minelogx-poc-legislation-documents"
}
import {
  to = aws_s3_bucket.amplify_deploy
  id = "minelogx-amplify-deploy"
}

# --- Compute: Lambda functions (DEFERRED) ---
# generate-config can't emit the code source and we lack the artifacts.
# Re-enable with a placeholder zip + lifecycle ignore_changes once backend/ exists.
# import {
#   to = aws_lambda_function.ml
#   id = "minelogx-lambda-ml-demo-poc"
# }
# import {
#   to = aws_lambda_function.rag
#   id = "minelogx-lambda-rag-demo-poc"
# }

# --- API Gateway (REST) ---
import {
  to = aws_api_gateway_rest_api.api
  id = "y6av2s1y2l"
}

# --- Amplify (frontend hosting) ---
import {
  to = aws_amplify_app.frontend
  id = "d3ncfjrzod7hm0"
}
import {
  to = aws_amplify_branch.frontend_demo
  id = "d3ncfjrzod7hm0/demo"
}

# ===========================================================================
# Connected untagged dependencies (mapped as part of the POC).
# ===========================================================================

# --- Networking: vpc-0a7b98533f5eaa246 ("vpc", tagged) ---
import {
  to = aws_subnet.hub_055a
  id = "subnet-055ac7f357e35d32e"
}
import {
  to = aws_subnet.hub_0b2a
  id = "subnet-0b2ae002ad7c5108a"
}
import {
  to = aws_subnet.hub_00e7
  id = "subnet-00e7da992d115c77a"
}
import {
  to = aws_subnet.hub_0855
  id = "subnet-08555f7b3cf31bece"
}
import {
  to = aws_route_table.hub_main
  id = "rtb-0e18a68efb4e16833"
}
import {
  to = aws_route_table.hub_a63
  id = "rtb-0a63207b303a4586a"
}
import {
  to = aws_route_table.hub_be1
  id = "rtb-0be19a6780b753040"
}
import {
  to = aws_route_table.hub_192
  id = "rtb-0192e8495b5e4f5e2"
}
import {
  to = aws_route_table_association.hub_0855
  id = "subnet-08555f7b3cf31bece/rtb-0a63207b303a4586a"
}
import {
  to = aws_route_table_association.hub_00e7
  id = "subnet-00e7da992d115c77a/rtb-0be19a6780b753040"
}
import {
  to = aws_route_table_association.hub_055a
  id = "subnet-055ac7f357e35d32e/rtb-0192e8495b5e4f5e2"
}
import {
  to = aws_route_table_association.hub_0b2a
  id = "subnet-0b2ae002ad7c5108a/rtb-0192e8495b5e4f5e2"
}
import {
  to = aws_internet_gateway.hub
  id = "igw-0e26839ad3b3d4b9c"
}
import {
  to = aws_vpc_endpoint.hub_s3
  id = "vpce-095ca4af58c7592e5"
}

# --- Networking: vpc-046d33367bbb17147 ("minelogx-demo-poc-vpc", real EC2 network) ---
import {
  to = aws_vpc.poc
  id = "vpc-046d33367bbb17147"
}
import {
  to = aws_subnet.poc_0061
  id = "subnet-0061661352e3c9ee8"
}
import {
  to = aws_subnet.poc_0f5f
  id = "subnet-0f5f6d7df9419de2c"
}
import {
  to = aws_subnet.poc_0200
  id = "subnet-0200910b0d82cedbc"
}
import {
  to = aws_subnet.poc_0d1d
  id = "subnet-0d1d2828040790453"
}
import {
  to = aws_route_table.poc_eea
  id = "rtb-0eea281b1fa57ba45"
}
import {
  to = aws_route_table.poc_b6d
  id = "rtb-0b6d1740e4231cde9"
}
import {
  to = aws_route_table.poc_b8b
  id = "rtb-0b8bec9796dbebc6b"
}
import {
  to = aws_route_table.poc_main
  id = "rtb-0ea7e8e23b45aa3e5"
}
import {
  to = aws_route_table_association.poc_0200
  id = "subnet-0200910b0d82cedbc/rtb-0eea281b1fa57ba45"
}
import {
  to = aws_route_table_association.poc_0d1d
  id = "subnet-0d1d2828040790453/rtb-0b6d1740e4231cde9"
}
import {
  to = aws_route_table_association.poc_0f5f
  id = "subnet-0f5f6d7df9419de2c/rtb-0b8bec9796dbebc6b"
}
import {
  to = aws_route_table_association.poc_0061
  id = "subnet-0061661352e3c9ee8/rtb-0b8bec9796dbebc6b"
}
import {
  to = aws_internet_gateway.poc
  id = "igw-0051a5cf44d7c2ebd"
}
import {
  to = aws_nat_gateway.poc
  id = "nat-03d94989de5658f43"
}
import {
  to = aws_eip.poc_nat
  id = "eipalloc-0f6711825d7a3dd49"
}
import {
  to = aws_vpc_endpoint.poc_s3
  id = "vpce-0a1e4d5e0a42af576"
}
import {
  to = aws_security_group.llm
  id = "sg-0a24aef3f7a1861f3"
}

# --- IAM roles + CloudWatch log groups for the (deferred) Lambdas ---
import {
  to = aws_iam_role.lambda_ml
  id = "minelogx-lambda-ml-demo-poc-role-n7ltl80h"
}
import {
  to = aws_iam_role.lambda_rag
  id = "minelogx-lambda-rag-demo-poc-role-cpugopla"
}
import {
  to = aws_cloudwatch_log_group.lambda_ml
  id = "/aws/lambda/minelogx-lambda-ml-demo-poc"
}
import {
  to = aws_cloudwatch_log_group.lambda_rag
  id = "/aws/lambda/minelogx-lambda-rag-demo-poc"
}

# Reference-only (NOT imported — shared/account-level, risky to own):
#   * KMS keys: 16a46304-adda-494e-aa21-14e472d82117 (EBS), 0dca84d0-... (AOSS)
#   * IAM instance profile: AmazonSSMRoleForInstancesQuickSetup (AWS QuickSetup)
# These stay as plain ID references in the resources that use them.

# --- OpenSearch Serverless collection (MOVED TO CLOUDFORMATION) ---
# TF aws provider mishandles dashboard_endpoint (provider bug) and the IaC strategy
# assigns OpenSearch Serverless to CloudFormation. Defined in
# onprem-aws/infrastructure/cloudformation/opensearch-serverless/ instead.
# import {
#   to = aws_opensearchserverless_collection.vectors
#   id = "2qdgmajvfh8mrvgzudv1"
# }

# ---------------------------------------------------------------------------
# REMAINING (intentionally not imported to Terraform here):
#  * Lambdas ml/rag — DEFERRED (need placeholder zip + lifecycle ignore_changes).
#  * AOSS collection + security policies + S3 Vectors bucket/index — CloudFormation.
#  * KMS keys & SSM instance profile — reference-only (shared/account-level).
# NEXT: refactor generated*.tf into ../../modules/* (parametrized), then instantiate
#       dev/qa/prod and finally delete the POC.
# ---------------------------------------------------------------------------
