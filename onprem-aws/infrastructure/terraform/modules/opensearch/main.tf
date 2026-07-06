# Amazon OpenSearch Serverless (VECTORSEARCH) collection — parity with the
# cloudformation/opensearch-serverless template. Central vector store for both
# pipelines. The vector indices (csv_telemetry_vecs 1024d Cohere, pdf_legal_vecs
# 1536d Titan) are DATA-PLANE objects created by the ingest Lambdas via the
# OpenSearch API — not by Terraform.
#
# NOTE: the aws provider historically returned collection_endpoint as unknown
# when IMPORTING an existing collection (see CLAUDE.md IaC Strategy — the
# imported demo keeps AOSS in CloudFormation). For NEW envs a fresh create is
# fine; the endpoint resolves at apply time.

variable "name_prefix" {
  type = string
}

variable "allow_public_network" {
  type    = bool
  default = true
}

variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  collection = "${var.name_prefix}-vectors"
}

resource "aws_opensearchserverless_security_policy" "encryption" {
  name = "${var.name_prefix}-enc"
  type = "encryption"
  policy = jsonencode({
    Rules       = [{ ResourceType = "collection", Resource = ["collection/${local.collection}"] }]
    AWSOwnedKey = true
  })
}

resource "aws_opensearchserverless_security_policy" "network" {
  name = "${var.name_prefix}-net"
  type = "network"
  policy = jsonencode([{
    Rules = [
      { ResourceType = "collection", Resource = ["collection/${local.collection}"] },
      { ResourceType = "dashboard", Resource = ["collection/${local.collection}"] },
    ]
    AllowFromPublic = var.allow_public_network
  }])
}

resource "aws_opensearchserverless_access_policy" "data" {
  name = "${var.name_prefix}-data"
  type = "data"
  # Any IAM principal in the account (subject to their IAM perms). Tighten to
  # specific ingest/agent role ARNs later.
  policy = jsonencode([{
    Rules = [
      { ResourceType = "index", Resource = ["index/${local.collection}/*"], Permission = ["aoss:*"] },
      { ResourceType = "collection", Resource = ["collection/${local.collection}"], Permission = ["aoss:*"] },
    ]
    Principal = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
  }])
}

data "aws_caller_identity" "current" {}

resource "aws_opensearchserverless_collection" "this" {
  name        = local.collection
  type        = "VECTORSEARCH"
  description = "MineLogX hybrid (kNN + BM25) vector store"
  tags        = merge(var.tags, { Name = local.collection })

  depends_on = [
    aws_opensearchserverless_security_policy.encryption,
    aws_opensearchserverless_security_policy.network,
    aws_opensearchserverless_access_policy.data,
  ]
}

output "collection_endpoint" {
  value = aws_opensearchserverless_collection.this.collection_endpoint
}

output "collection_arn" {
  value = aws_opensearchserverless_collection.this.arn
}

# Host without scheme — matches what the pipeline OPENSEARCH_HOST expects.
output "collection_host" {
  value = replace(aws_opensearchserverless_collection.this.collection_endpoint, "https://", "")
}
