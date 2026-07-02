# Terraform import blocks

This folder holds `import {}` blocks (Terraform >= 1.5) that adopt the
hand-deployed demo into Terraform state, plus the config generated from them.

## Workflow (run after `scripts/discover-aws.sh` populates `../../discovery/`)

1. Fill in `demo-imports.tf` with one `import` block per real resource, using
   the IDs/ARNs found in `infrastructure/discovery/*.json`. Example:

   ```hcl
   import {
     to = aws_lambda_function.minelogx_kpis
     id = "minelogx-kpis"
   }
   import {
     to = aws_s3_bucket.data_lake
     id = "iot-mining-poc"
   }
   ```

2. Generate HCL for the imported resources:

   ```bash
   cd infrastructure/terraform/environments/_imported-demo
   terraform plan -generate-config-out=generated.tf
   ```

3. Review `generated.tf`, refactor it into clean `../../modules/*`, then:

   ```bash
   terraform apply     # adopts state; must show only imports, no destroys
   terraform plan      # must report 0 changes (faithful capture of the demo)
   ```

`generated.tf` is a scratch artifact — do not commit it as-is; fold it into modules.
