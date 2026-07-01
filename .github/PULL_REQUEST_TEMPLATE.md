<!-- Title must follow: [BHMIB-<ticket>] <type>: <short description> -->

## What changed and why
<!-- Summary of the change and the problem it solves -->

## Ticket
BHMIB-

## Type
<!-- feat | fix | chore | refactor | docs | test | infra | ci | fab -->

## How to test / verify
<!-- Commands, `fab env.plan`, terraform plan output, screenshots... -->

## Infrastructure impact
- [ ] No infra change
- [ ] Terraform and CloudFormation definitions kept at parity
- [ ] Manual steps required (describe below)
- [ ] Fabric tasks affected (list below)

## Checklist
- [ ] Branched from the correct base, lowercase-hyphen name
- [ ] Atomic commits in `[BHMIB-<ticket>] <type>: <desc>` format
- [ ] No secrets / credentials committed
- [ ] Linters pass (ruff, bandit, yamllint, eslint/tsc where applicable)
