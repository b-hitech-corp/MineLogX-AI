"""
MineLogX-AI — Fabric automation.

Two responsibilities:

1. Environment orchestration (`env.*`): create / destroy / plan / list
   infrastructure environments through EITHER Terraform OR CloudFormation,
   selected with `--engine`. Supports both fixed environments (dev/qa/prod)
   and ephemeral per-developer environments (`dev-<user>`).

2. Remote ops on the POC Ollama EC2 instances (`ollama.*`): health checks,
   restarts, model pulls, log tailing — the original Fabric use case.

Conventions:
  - Resource name prefix / stack prefix:  minelogx-<env>
  - Mandatory tags on every resource: aws-apn-id, Environment, ManagedBy
  - AWS profile is taken from the AWS_PROFILE env var (see AWS CLI setup).

Usage (env and engine are positional; engine defaults to terraform):
  fab env.up   dev-cesar          # terraform (default)
  fab env.plan dev-cesar cf       # cloudformation  (aliases: tf / cf)
  fab env.down dev-cesar
  fab env.list
  fab ollama.health-check

Tip: activate the venv to drop the `uv run` prefix —
  source .venv/Scripts/activate   # Windows/Git Bash;  .venv/bin/activate on macOS/Linux
Or add a shell alias:  alias mlx='uv run fab'   ->   mlx env.plan dev-cesar cf
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
PROD_APN_ID = "pc:925kllxsozl58ehxuk1rxxd8z"  # PROD uses a distinct APN id
NAME_PREFIX = "minelogx"

# Terraform remote state (bootstrap once with scripts/bootstrap-backend.sh).
STATE_BUCKET = os.environ.get("TF_STATE_BUCKET", "minelogx-terraform-state")
STATE_LOCK_TABLE = os.environ.get("TF_STATE_LOCK_TABLE", "minelogx-terraform-locks")

REPO_ROOT = Path(__file__).resolve().parent
# Deployment target (framework layout). Override with MINELOGX_TARGET.
TARGET = os.environ.get("MINELOGX_TARGET", "onprem-aws")
TARGET_ROOT = REPO_ROOT / TARGET
TF_ROOT = TARGET_ROOT / "infrastructure" / "terraform"
TF_ENVS = TF_ROOT / "environments"
CFN_ROOT = TARGET_ROOT / "infrastructure" / "cloudformation"

# CloudFormation layers deployed per environment, in dependency order.
CFN_LAYERS = [
    "network",
    "security-groups",
    "s3",
    "iam",
    "cloudwatch",
    "lambda",
    "apigw",
    "ec2",
    "eventbridge",
    "step-functions",
    "opensearch-serverless",
    "bedrock-guardrails",
    "amplify",
]

# Fixed environments have their own Terraform root module under environments/.
FIXED_ENVS = {"dev", "qa", "prod"}

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
    # Fixed envs get their own state key; ephemeral envs share one key and are
    # isolated by Terraform workspace.
    state_key = (
        f"{TARGET}/{env if env in FIXED_ENVS else 'ephemeral'}/terraform.tfstate"
    )
    with c.cd(workdir):
        c.run(
            "terraform init -input=false -reconfigure "
            f'-backend-config="bucket={STATE_BUCKET}" '
            f'-backend-config="key={state_key}" '
            f'-backend-config="region={REGION}" '
            f'-backend-config="dynamodb_table={STATE_LOCK_TABLE}" '
            f'-backend-config="encrypt=true"'
        )
        if _is_ephemeral(env):
            # `|| true` so re-selecting an existing workspace is idempotent.
            c.run(f"terraform workspace select {env} || terraform workspace new {env}")
        c.run(" ".join(["terraform", *args]))


def _cfn_stack(env, layer):
    return f"{NAME_PREFIX}-{env}-{layer}"


# CFN parameters are computed inline per environment in up()/plan() so that
# ephemeral dev-<user> stacks get a unique NamePrefix. The params/*.json files
# are kept for manual `aws cloudformation deploy` and advanced per-env overrides.


def _norm_engine(engine):
    """Accept short aliases: tf -> terraform, cf -> cloudformation."""
    engine = {"tf": "terraform", "cf": "cloudformation"}.get(engine, engine)
    if engine not in ("terraform", "cloudformation"):
        raise SystemExit("Error: engine must be terraform|tf or cloudformation|cf.")
    return engine


def _apn(env):
    """Project aws-apn-id tag. PROD uses a distinct APN id (see PROD_APN_ID)."""
    return PROD_APN_ID if env == "prod" else PROJECT_APN_ID


def _cfn(c, env, execute):
    """Package + deploy the single parent stack (nested children) as minelogx-<env>.

    `package` uploads the child templates to S3 and rewrites TemplateURLs.
    execute=False creates a change set without applying (plan).
    """
    apn = _apn(env)
    fallback = "true" if env == "prod" else "false"
    with c.cd(str(CFN_ROOT)):
        c.run(
            "aws cloudformation package --template-file parent.yaml "
            f"--s3-bucket {STATE_BUCKET} --s3-prefix cfn/{env} "
            f"--output-template-file packaged-parent.yaml --region {REGION}"
        )
        cmd = (
            "aws cloudformation deploy --template-file packaged-parent.yaml "
            f"--stack-name {NAME_PREFIX}-{env} "
            f"--parameter-overrides NamePrefix={NAME_PREFIX}-{env} Environment={env} "
            f"ProjectApnId={apn} EnableLlmFallback={fallback} "
            f"--tags aws-apn-id={apn} Environment={env} ManagedBy=cloudformation "
            f"--capabilities CAPABILITY_NAMED_IAM --region {REGION}"
        )
        if not execute:
            cmd += " --no-execute-changeset"
        c.run(cmd)


# --------------------------------------------------------------------------- #
# env.* — environment orchestration
# --------------------------------------------------------------------------- #
@task(
    help={
        "env": "Environment name (dev|qa|prod|dev-<user>).",
        "engine": "terraform | cloudformation",
    }
)
def up(c, env, engine="terraform"):
    """Create/update an environment (fixed or ephemeral)."""
    engine = _norm_engine(engine)
    print(f"==> up: env={env} engine={engine} region={REGION}")

    if engine == "terraform":
        _tf(
            c,
            env,
            "apply",
            "-auto-approve",
            f'-var="environment={env}"',
            f'-var="aws_region={REGION}"',
            f'-var="name_prefix={NAME_PREFIX}-{env}"',
            f'-var="project_apn_id={_apn(env)}"',
        )
        return

    _cfn(c, env, execute=True)


@task(help={"env": "Environment name.", "engine": "terraform | cloudformation"})
def plan(c, env, engine="terraform"):
    """Preview changes without applying (terraform plan / CFN change set)."""
    engine = _norm_engine(engine)
    print(f"==> plan: env={env} engine={engine}")

    if engine == "terraform":
        _tf(
            c,
            env,
            "plan",
            f'-var="environment={env}"',
            f'-var="aws_region={REGION}"',
            f'-var="name_prefix={NAME_PREFIX}-{env}"',
            f'-var="project_apn_id={_apn(env)}"',
        )
        return

    _cfn(c, env, execute=False)


@task(help={"env": "Environment name.", "engine": "terraform | cloudformation"})
def down(c, env, engine="terraform"):
    """Destroy an environment. Guarded against fixed prod."""
    engine = _norm_engine(engine)
    if env == "prod":
        raise SystemExit(
            "Refusing to destroy 'prod'. Tear it down manually if you must."
        )
    print(f"==> down: env={env} engine={engine}")

    if engine == "terraform":
        _tf(
            c,
            env,
            "destroy",
            "-auto-approve",
            f'-var="environment={env}"',
            f'-var="aws_region={REGION}"',
            f'-var="name_prefix={NAME_PREFIX}-{env}"',
            f'-var="project_apn_id={_apn(env)}"',
        )
        if _is_ephemeral(env):
            with c.cd(_tf_workdir(env)):
                c.run("terraform workspace select default")
                c.run(f"terraform workspace delete {env} || true")
        return

    # CloudFormation: one parent stack — deleting it removes all nested children.
    c.run(
        f"aws cloudformation delete-stack --stack-name {NAME_PREFIX}-{env} --region {REGION}"
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
        conn = Connection(
            host, user=SSH_USER, connect_kwargs={"key_filename": KEY_PATH}
        )
        result = conn.run(
            "curl -s http://localhost:11434/api/tags", hide=True, warn=True
        )
        print(f"{name}: {'OK' if result.ok else 'FAILED'}")


@task(name="restart-ollama")
def restart_ollama(c):
    """Restart the Ollama Docker container on all instances."""
    group = SerialGroup(
        *INSTANCES.values(), user=SSH_USER, connect_kwargs={"key_filename": KEY_PATH}
    )
    group.run("docker restart ollama")


@task(
    name="pull-model",
    help={
        "host": "Instance key (qwen3|gemma3|embeddings).",
        "model": "Model tag, e.g. qwen3:8b",
    },
)
def pull_model(c, host, model):
    """Pull a new model on a specific instance."""
    conn = Connection(
        INSTANCES[host], user=SSH_USER, connect_kwargs={"key_filename": KEY_PATH}
    )
    conn.run(f"ollama pull {model}")


@task(help={"host": "Instance key (qwen3|gemma3|embeddings)."})
def logs(c, host):
    """Tail Ollama container logs on a specific instance."""
    conn = Connection(
        INSTANCES[host], user=SSH_USER, connect_kwargs={"key_filename": KEY_PATH}
    )
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
