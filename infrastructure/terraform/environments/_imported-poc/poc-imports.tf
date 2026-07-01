# POC import blocks — populated in Phase 2 from infrastructure/discovery/*.json.
#
# See ../../imports/README.md for the full workflow. Example:
#
# import {
#   to = aws_s3_bucket.data_lake
#   id = "iot-mining-poc"
# }
#
# import {
#   to = aws_lambda_function.minelogx_kpis
#   id = "minelogx-kpis"
# }
#
# After adding blocks:
#   terraform plan -generate-config-out=generated.tf
#   terraform apply           # adopt state (imports only, no destroys)
#   terraform plan            # must show 0 changes
