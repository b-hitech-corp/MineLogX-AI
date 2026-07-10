# AWS Authentication

All Fabric tasks assume an AWS CLI profile named **`minelogx-admin`** that has access to the
POC account (`586928288932`) via SSO assume-role.

---

## One-time SSO profile setup

Run this once per machine:

```bash
aws configure sso --profile minelogx-admin
# SSO start URL : https://d-9067e84741.awsapps.com/start
# SSO region    : us-east-1
# Account       : 586928288932
# Role          : AvahiAdminAccess
```

---

## Per-session login

The SSO token expires after ~8 hours. Re-run at the start of each work session:

```bash
aws sso login --profile minelogx-admin
```

Verify the session is active:

```bash
aws sts get-caller-identity --profile minelogx-admin
# Should show Account: 586928288932
```

---

## Environment variable overrides

| Variable | Default | Purpose |
|---|---|---|
| `MINELOGX_AWS_PROFILE` | `minelogx-admin` | AWS CLI profile for all Fabric operations |
| `AWS_REGION` | `us-east-1` | Target region |
| `CFN_TEMPLATE_BUCKET` | `minelogx-poc-cfn-templates` | S3 bucket for nested CFN template uploads |
| `AWS_SSO_PROFILE` | `125396563242_B_Hitech-586928288932` | SSO hub profile for auto-token refresh |
| `MINELOGX_TARGET` | `onprem-aws` | Deployment target folder |
| `EC2_KEY_PATH` | `~/.ssh/minelogx-demo-poc-keypair.pem` | SSH key for Ollama EC2 instances |

Override for CI or a different account:

```bash
export MINELOGX_AWS_PROFILE=my-other-profile
export AWS_REGION=us-west-2
```

---

!!! warning "Scope the profile per shell"
    Use a dedicated named profile instead of setting `AWS_PROFILE` globally — this prevents
    accidentally running commands against the wrong account in other terminal tabs.
