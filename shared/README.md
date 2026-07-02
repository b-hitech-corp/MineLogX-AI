# shared

Cross-target, reusable code shared by all deployment targets (`onprem-aws`,
`onprem-azure`, `onprem-ibm`, …). Keep anything cloud-agnostic here so targets
don't duplicate it.

```
shared/
├── modules/       # cloud-agnostic building blocks
├── connectors/    # protocol adapters reusable across targets
└── templates/     # shared config / IaC / doc templates
```

Only `onprem-aws` and `shared` exist today; other targets are added when a
client actually needs them.
