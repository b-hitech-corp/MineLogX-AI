"""
MineLogX-AI — Fabric automation.

Two responsibilities:

1. Environment orchestration (`env.*`): create / destroy / plan / list
   infrastructure environments through EITHER Terraform OR CloudFormation,
   selected with `--engine`. Supports both fixed environments (dev/staging/prod)
   and ephemeral per-developer environments (`dev-<user>`).

2. Remote ops on the POC Ollama EC2 instances (`ollama.*`): health checks,
   restarts, model pulls, log tailing — the original Fabric use case.

Conventions:
  - Resource name prefix / stack prefix:  minelogx-<env>
  - Mandatory tags on every resource: aws-apn-id, Environment, ManagedBy
  - AWS profile is taken from the AWS_PROFILE env var (see AWS CLI setup).

Usage:
  fab --list
  fab env.up   --env=dev-cesar --engine=terraform
  fab env.plan --env=staging   --engine=cloudformation
  fab env.down --env=dev-cesar --engine=terraform
  fab env.list
  fab ollama.health-check
"""

import os
from pathlib import Path

from fabric import Connection, SerialGroup, task
from invoke import Collection

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
REGION = os.environ.get("AWS_REGION", "us-east-1")
PROJECT_APN_ID = "pc:13uw3s8iyvze74tlcq3o0w8r6"
NAME_PREFIX = "minelogx"

REPO_ROOT = Path(__file__).resolve().parent
TF_ROOT = REPO_ROOT / "infrastructure" / "terraform"
TF_ENVS = TF_ROOT / "environments"
CFN_ROOT = REPO_ROOT / "infrastructure" / "cloudformation"

# CloudFormation layers deployed per environment, in dependency order.
CFN_LAYERS = [
    "network",
    "s3",
    "iam",
    "lambda",
    "apigw",
    "eventbridge",
    "step-functions",
    "opensearch-serverless",
    "bedrock-guardrails",
]

# Fixed environments have their own Terraform root module under environments/.
FIXED_ENVS = {"dev", "staging", "prod"}

# POC Ollama EC2 instances (see CLAUDE.md — POC only, to be replaced by Bedrock).
INSTANCES = {
    "qwen3": "ec2-98-81-228-187.compute-1.amazonaws.com",
    "gemma3": "ec2-100-31-82-64.compute-1.amazonaws.com",
    "embeddings": "ec2-3-208-23-94.compute-1.amazonaws.com",
}
KEY_PATH = os.environ.get("EC2_KEY_PATH", "~/.ssh/minelogx-demo-poc-keypair.pem")
SSH_USER = "ubuntu"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _is_ephemeral(env):
    """Ephemeral envs are per-developer, named dev-<user> (not a fixed env)."""
    return env not in FIXED_ENVS


def _tf_workdir(env):
    """
    Terraform working directory for an environment.
    Fixed envs use their own root module; ephemeral envs share the `ephemeral`
    root module and are isolated by a Terraform workspace named after the env.
    """
    return str(TF_ENVS / (env if env in FIXED_ENVS else "ephemeral"))


def _tf(c, env, *args):
    """Run terraform in the env's working dir, selecting a workspace if ephemeral."""
    workdir = _tf_workdir(env)
    with c.cd(workdir):
        c.run("terraform init -input=false")
        if _is_ephemeral(env):
            # `|| true` so re-selecting an existing workspace is idempotent.
            c.run(f"terraform workspace select {env} || terraform workspace new {env}")
        c.run(" ".join(["terraform", *args]))


def _cfn_stack(env, layer):
    return f"{NAME_PREFIX}-{env}-{layer}"


def _cfn_params(env):
    """Parameter file for the environment (falls back to the base env name)."""
    candidates = [CFN_ROOT / "params" / f"{env}.params.json"]
    if _is_ephemeral(env):
        candidates.append(CFN_ROOT / "params" / f"{env.split('-')[0]}.params.json")
    for p in candidates:
        if p.exists():
            return p
    return None


def _require_engine(engine):
    if engine not in ("terraform", "cloudformation"):
        raise SystemExit("Error: --engine must be 'terraform' or 'cloudformation'.")


# --------------------------------------------------------------------------- #
# env.* — environment orchestration
# --------------------------------------------------------------------------- #
@task(help={"env": "Environment name (dev|staging|prod|dev-<user>).",
            "engine": "terraform | cloudformation"})
def up(c, env, engine="terraform"):
    """Create/update an environment (fixed or ephemeral)."""
    _require_engine(engine)
    print(f"==> up: env={env} engine={engine} region={REGION}")

    if engine == "terraform":
        _tf(c, env, "apply", "-auto-approve",
            f'-var="environment={env}"',
            f'-var="aws_region={REGION}"',
            f'-var="name_prefix={NAME_PREFIX}-{env}"')
        return

    params = _cfn_params(env)
    param_arg = f"--parameter-overrides file://{params}" if params else ""
    for layer in CFN_LAYERS:
        template = CFN_ROOT / layer / f"{layer}.yaml"
        if not template.exists():
            print(f"    (skip {layer}: {template} not present yet)")
            continue
        c.run(
            f"aws cloudformation deploy "
            f"--template-file {template} "
            f"--stack-name {_cfn_stack(env, layer)} "
            f"{param_arg} "
            f"--tags aws-apn-id={PROJECT_APN_ID} Environment={env} ManagedBy=cloudformation "
            f"--capabilities CAPABILITY_NAMED_IAM "
            f"--region {REGION}"
        )


@task(help={"env": "Environment name.", "engine": "terraform | cloudformation"})
def plan(c, env, engine="terraform"):
    """Preview changes without applying (terraform plan / CFN change set)."""
    _require_engine(engine)
    print(f"==> plan: env={env} engine={engine}")

    if engine == "terraform":
        _tf(c, env, "plan",
            f'-var="environment={env}"',
            f'-var="aws_region={REGION}"',
            f'-var="name_prefix={NAME_PREFIX}-{env}"')
        return

    params = _cfn_params(env)
    param_arg = f"--parameter-overrides file://{params}" if params else ""
    for layer in CFN_LAYERS:
        template = CFN_ROOT / layer / f"{layer}.yaml"
        if not template.exists():
            continue
        c.run(
            f"aws cloudformation deploy --no-execute-changeset "
            f"--template-file {template} "
            f"--stack-name {_cfn_stack(env, layer)} "
            f"{param_arg} --capabilities CAPABILITY_NAMED_IAM --region {REGION}"
        )


@task(help={"env": "Environment name.", "engine": "terraform | cloudformation"})
def down(c, env, engine="terraform"):
    """Destroy an environment. Guarded against fixed prod."""
    _require_engine(engine)
    if env == "prod":
        raise SystemExit("Refusing to destroy 'prod'. Tear it down manually if you must.")
    print(f"==> down: env={env} engine={engine}")

    if engine == "terraform":
        _tf(c, env, "destroy", "-auto-approve",
            f'-var="environment={env}"',
            f'-var="aws_region={REGION}"',
            f'-var="name_prefix={NAME_PREFIX}-{env}"')
        if _is_ephemeral(env):
            with c.cd(_tf_workdir(env)):
                c.run("terraform workspace select default")
                c.run(f"terraform workspace delete {env} || true")
        return

    # CloudFormation: delete stacks in reverse dependency order.
    for layer in reversed(CFN_LAYERS):
        c.run(
            f"aws cloudformation delete-stack "
            f"--stack-name {_cfn_stack(env, layer)} --region {REGION} || true"
        )


@task
def list(c):
    """List active environments (Terraform workspaces + minelogx-* CFN stacks)."""
    print("== Terraform workspaces (ephemeral) ==")
    with c.cd(str(TF_ENVS / "ephemeral")):
        c.run("terraform workspace list || true")
    print("\n== CloudFormation stacks (minelogx-*) ==")
    c.run(
        f"aws cloudformation list-stacks --region {REGION} "
        f"--stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE "
        f"--query \"StackSummaries[?starts_with(StackName, '{NAME_PREFIX}-')].StackName\" "
        f"--output table || true"
    )


# --------------------------------------------------------------------------- #
# ollama.* — POC EC2 remote ops (unchanged use case)
# --------------------------------------------------------------------------- #
@task(name="health-check")
def health_check(c):
    """Check all Ollama instances are responding."""
    for name, host in INSTANCES.items():
        conn = Connection(host, user=SSH_USER,
                          connect_kwargs={"key_filename": KEY_PATH})
        result = conn.run("curl -s http://localhost:11434/api/tags", hide=True, warn=True)
        print(f"{name}: {'OK' if result.ok else 'FAILED'}")


@task(name="restart-ollama")
def restart_ollama(c):
    """Restart the Ollama Docker container on all instances."""
    group = SerialGroup(*INSTANCES.values(), user=SSH_USER,
                        connect_kwargs={"key_filename": KEY_PATH})
    group.run("docker restart ollama")


@task(name="pull-model", help={"host": "Instance key (qwen3|gemma3|embeddings).",
                               "model": "Model tag, e.g. qwen3:8b"})
def pull_model(c, host, model):
    """Pull a new model on a specific instance."""
    conn = Connection(INSTANCES[host], user=SSH_USER,
                      connect_kwargs={"key_filename": KEY_PATH})
    conn.run(f"ollama pull {model}")


@task(help={"host": "Instance key (qwen3|gemma3|embeddings)."})
def logs(c, host):
    """Tail Ollama container logs on a specific instance."""
    conn = Connection(INSTANCES[host], user=SSH_USER,
                      connect_kwargs={"key_filename": KEY_PATH})
    conn.run("docker logs --tail 100 ollama")


# --------------------------------------------------------------------------- #
# Namespaces
# --------------------------------------------------------------------------- #
env = Collection("env")
env.add_task(up)
env.add_task(plan)
env.add_task(down)
env.add_task(list)

ollama = Collection("ollama")
ollama.add_task(health_check)
ollama.add_task(restart_ollama)
ollama.add_task(pull_model)
ollama.add_task(logs)

ns = Collection()
ns.add_collection(env)
ns.add_collection(ollama)
