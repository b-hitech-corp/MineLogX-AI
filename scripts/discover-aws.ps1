<#
.SYNOPSIS
  Read-only inventory of the MineLogX-AI POC deployed in AWS (PowerShell variant).

.DESCRIPTION
  Dumps the current state of the account (filtered by the project tag) into
  infrastructure/discovery/ so it can be reverse-engineered into IaC.
  Performs NO mutations — only list-*/describe-*/get-* calls.

.EXAMPLE
  $env:AWS_PROFILE = "minelogx"; ./scripts/discover-aws.ps1
  ./scripts/discover-aws.ps1 -Region us-east-1
#>
param(
  [string]$Region = "us-east-1",
  [string]$Out    = "infrastructure/discovery"
)

$ErrorActionPreference = "Continue"

$ProjectTagKey   = "aws-apn-id"
$ProjectTagValue = "pc:13uw3s8iyvze74tlcq3o0w8r6"
$TagFilter       = "Key=$ProjectTagKey,Values=$ProjectTagValue"

New-Item -ItemType Directory -Force -Path $Out | Out-Null

function Dump {
  param([string]$File, [string[]]$AwsArgs)
  Write-Host "  -> $File"
  $target = Join-Path $Out $File
  $err    = "$target.err"
  & aws @AwsArgs > $target 2> $err
  if ($LASTEXITCODE -ne 0) {
    Write-Host "     (warning: command failed — see $err)"
  } elseif (Test-Path $err) {
    Remove-Item $err -ErrorAction SilentlyContinue
  }
}

Write-Host "== MineLogX-AI AWS discovery =="
Write-Host "Region : $Region"
Write-Host "Tag    : $ProjectTagKey=$ProjectTagValue"
Write-Host "Output : $Out`n"

Write-Host "[identity]"
Dump "identity.json"               @("sts","get-caller-identity")

Write-Host "[tag inventory]"
Dump "tagged-resources.json"       @("resourcegroupstaggingapi","get-resources","--tag-filters",$TagFilter,"--region",$Region)
Dump "resource-explorer.json"      @("resource-explorer-2","search","--query-string","tag:$ProjectTagKey=$ProjectTagValue","--region",$Region)

Write-Host "[networking]"
Dump "vpcs.json"                    @("ec2","describe-vpcs","--region",$Region)
Dump "subnets.json"                 @("ec2","describe-subnets","--region",$Region)
Dump "security-groups.json"         @("ec2","describe-security-groups","--region",$Region)
Dump "route-tables.json"            @("ec2","describe-route-tables","--region",$Region)
Dump "internet-gateways.json"       @("ec2","describe-internet-gateways","--region",$Region)
Dump "nat-gateways.json"            @("ec2","describe-nat-gateways","--region",$Region)

Write-Host "[compute]"
Dump "ec2-instances.json"           @("ec2","describe-instances","--region",$Region)
Dump "key-pairs.json"               @("ec2","describe-key-pairs","--region",$Region)

Write-Host "[lambda]"
Dump "lambdas.json"                 @("lambda","list-functions","--region",$Region)

Write-Host "[api gateway]"
Dump "apigw-rest.json"              @("apigateway","get-rest-apis","--region",$Region)
Dump "apigw-http.json"              @("apigatewayv2","get-apis","--region",$Region)

Write-Host "[s3]"
Dump "s3-buckets.json"              @("s3api","list-buckets")

Write-Host "[iam]"
Dump "iam-roles.json"               @("iam","list-roles")
Dump "iam-policies.json"            @("iam","list-policies","--scope","Local")

Write-Host "[eventbridge / step functions]"
Dump "eventbridge-rules.json"       @("events","list-rules","--region",$Region)
Dump "eventbridge-schedulers.json"  @("scheduler","list-schedules","--region",$Region)
Dump "stepfunctions.json"           @("stepfunctions","list-state-machines","--region",$Region)

Write-Host "[opensearch / bedrock]"
Dump "opensearch-serverless.json"   @("opensearchserverless","list-collections","--region",$Region)
Dump "opensearch-domains.json"      @("opensearch","list-domain-names","--region",$Region)
Dump "bedrock-guardrails.json"      @("bedrock","list-guardrails","--region",$Region)

Write-Host "[cloudwatch logs]"
Dump "log-groups.json"              @("logs","describe-log-groups","--region",$Region)

Write-Host "`nDone. Inventory written to $Out/"
Write-Host "Share the contents of $Out/ (it is gitignored) to generate the IaC."
