# CloudFormation parameters

One file per environment: `<env>.params.json` (e.g. `dev.params.json`,
`staging.params.json`, `dev-cesar.params.json`).

Copy `dev.params.json.example` to get started. Files matching
`*.local.json` are gitignored for developer-specific overrides.

Every environment must set at least `Environment`, `NamePrefix`, and
`ProjectApnId` (the `aws-apn-id` tag value).
