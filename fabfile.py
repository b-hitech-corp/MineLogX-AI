"""
MineLogX-AI — Fabric automation.

Three responsibilities:

1. Environment orchestration (`env.*`): create / destroy / plan / list
   infrastructure environments via CloudFormation (primary). Terraform is
   available as an alternative with `--engine terraform` but CloudFormation
   is the default — it gives full stack visibility in the AWS console.

2. Frontend deployment (`frontend.*`): build the React/Vite app from
   shared/frontend/ and push it to the Amplify app for the target environment.

3. Remote ops on the demo Ollama EC2 instances (`ollama.*`): health checks,
   restarts, model pulls, log tailing — the original Fabric use case.

Conventions:
  - Resource name prefix / stack prefix:  minelogx-<env>
  - CloudFormation stack: one parent stack (minelogx-<env>) with nested children.
  - Mandatory tags on every resource: aws-apn-id, Environment, ManagedBy
  - AWS profile is taken from the AWS_PROFILE env var (see AWS CLI setup).

Usage (env is positional; engine defaults to cloudformation):
  uv run fab env.up   dev                          # cloudformation deploy (default)
  uv run fab env.plan dev                          # cloudformation change set (no apply)
  uv run fab env.down dev
  uv run fab env.list
  uv run fab frontend.deploy dev                   # build + push frontend to Amplify
  uv run fab ollama.health-check
  uv run fab lambda.pull                           # download the demo Lambda code
  uv run fab lambda.build-layer pdf                # build the PDF deps layer (no Docker)
  uv run fab lambda.invoke csv dev --wait          # trigger CSV pipeline (Step Functions)
  uv run fab lambda.invoke pdf dev                 # trigger PDF pipeline (Lambda direct)

Full DEV flow (first time):
  uv run fab env.bootstrap                         # create S3 bucket (for nested template uploads)
  uv run fab lambda.build-layer csv
  uv run fab lambda.build-layer pdf
  uv run fab env.up dev --seed                     # cloudformation deploy + seed from demo buckets
  uv run fab env.up dev                            # cloudformation deploy (clean buckets, no seed)
  uv run fab frontend.deploy dev                   # build React + push to Amplify

Note: use the long flag `--engine` — Fabric reserves the short `-e` for --echo.

Tip: prefer `uv run fab` — it finds terraform via the shell PATH regardless of
venv activation. If terraform still isn't found, set TERRAFORM_BIN (see below).
"""

import datetime
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
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

# Resolve the terraform binary in an OS-agnostic way. Precedence:
#   1. TERRAFORM_BIN env var (bulletproof override — survives venv/PATH quirks)
#   2. terraform on PATH (shutil.which — works on Windows/Linux/Mac)
#   3. bare "terraform" fallback
# We pass the resolved path to Fabric so its subprocess finds terraform even when
# PATH is mangled by a venv activate script or cmd.exe vs Git Bash differences.
TERRAFORM = os.environ.get("TERRAFORM_BIN") or shutil.which("terraform") or "terraform"

# S3 bucket used by `cloudformation package` to upload nested templates.
# Also reused by Terraform state if `--engine terraform` is ever needed.
STATE_BUCKET = os.environ.get("CFN_TEMPLATE_BUCKET", "minelogx-poc-cfn-templates")

# SSO profile used to auto-refresh the token (override per dev with AWS_SSO_PROFILE).
# NOTE: this is the SSO *hub* account (125396563242) — only an identity source.
SSO_LOGIN_PROFILE = os.environ.get(
    "AWS_SSO_PROFILE", "125396563242_B_Hitech-586928288932"
)

# Profile used for ALL AWS operations. It assume-roles into the POC account
# (586928288932); the SSO login above is only the identity source. We FORCE it
# (rather than trust an ambient AWS_PROFILE, which often points at the SSO hub
# and would silently hit the wrong account). Override per dev/CI with
# MINELOGX_AWS_PROFILE (e.g. when PROD moves to its own account).
WORK_PROFILE = os.environ.get("MINELOGX_AWS_PROFILE", "minelogx-admin")
os.environ["AWS_PROFILE"] = WORK_PROFILE

REPO_ROOT = Path(__file__).resolve().parent
LOGS_DIR = REPO_ROOT / ".fab-logs"
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

# Demo buckets used as seed data source for DEV environments.
# Mapping: bucket name suffix -> existing demo bucket name.
DEMO_SEED_BUCKETS = {
    "telemetry-data": "bhitech-minelogx-poc-telemetry-data",
    "legislation-documents": "bhitech-minelogx-poc-legislation-documents",
}
# Only these environments may receive seed data; all others are always clean.
SEED_ALLOWED_ENVS = {"dev"}

# demo Ollama EC2 instances (see CLAUDE.md — demo only, to be replaced by Bedrock).
INSTANCES = {
    "qwen3": "ec2-98-81-228-187.compute-1.amazonaws.com",
    "gemma3": "ec2-100-31-82-64.compute-1.amazonaws.com",
    "embeddings": "ec2-3-208-23-94.compute-1.amazonaws.com",
}
KEY_PATH = os.path.expanduser(
    os.environ.get("EC2_KEY_PATH", "~/.ssh/minelogx-demo-poc-keypair.pem")
)
SSH_USER = "ubuntu"


# --------------------------------------------------------------------------- #
# Terminal color helpers (ANSI — supported by Git Bash, macOS, Linux terminals)
# --------------------------------------------------------------------------- #
_ANSI_RESET = "\033[0m"
_ANSI_GREEN = "\033[32m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RED = "\033[31m"
_ANSI_BOLD = "\033[1m"
_ANSI_DIM = "\033[2m"
_ANSI_CYAN = "\033[36m"

# Detect if the terminal supports color (disable in CI or when piped).
_COLOR = os.environ.get("NO_COLOR") is None and os.environ.get("TERM") != "dumb"


def _green(s):
    return f"{_ANSI_GREEN}{s}{_ANSI_RESET}" if _COLOR else s


def _yellow(s):
    return f"{_ANSI_YELLOW}{s}{_ANSI_RESET}" if _COLOR else s


def _red(s):
    return f"{_ANSI_RED}{s}{_ANSI_RESET}" if _COLOR else s


def _bold(s):
    return f"{_ANSI_BOLD}{s}{_ANSI_RESET}" if _COLOR else s


def _dim(s):
    return f"{_ANSI_DIM}{s}{_ANSI_RESET}" if _COLOR else s


def _cyan(s):
    return f"{_ANSI_CYAN}{s}{_ANSI_RESET}" if _COLOR else s


def _ok(label=""):
    return _green(f"OK  {label}".strip())


def _warn(label=""):
    return _yellow(f"WARN {label}".strip())


def _err(label=""):
    return _red(f"ERR  {label}".strip())


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
            f'"{TERRAFORM}" init -input=false -reconfigure '
            f'-backend-config="bucket={STATE_BUCKET}" '
            f'-backend-config="key={state_key}" '
            f'-backend-config="region={REGION}" '
            f'-backend-config="dynamodb_table=minelogx-poc-terraform-locks" '
            f'-backend-config="encrypt=true"'
        )
        if _is_ephemeral(env):
            # Select the workspace, creating it on first use (shell-agnostic —
            # no `||`, which breaks under cmd.exe on Windows).
            if not c.run(f'"{TERRAFORM}" workspace select {env}', warn=True).ok:
                c.run(f'"{TERRAFORM}" workspace new {env}')
        c.run(" ".join([f'"{TERRAFORM}"', *args]))


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


def _ensure_aws(c):
    """Auto-refresh the SSO token if missing/expired (opens the browser once).

    SSO requires an interactive login, so this can't be fully silent — but it
    triggers `aws sso login` for you instead of failing with an expired token.
    """
    ident = c.run(
        "aws sts get-caller-identity --query Account --output text",
        hide=True,
        warn=True,
    )
    if not ident.ok:
        print("==> AWS SSO token missing/expired — refreshing (a browser may open)...")
        c.run(f"aws sso login --profile {SSO_LOGIN_PROFILE}")
        ident = c.run(
            "aws sts get-caller-identity --query Account --output text",
            hide=True,
            warn=True,
        )
    account = ident.stdout.strip() if ident.ok else "unknown"
    print(f"==> AWS profile={WORK_PROFILE} account={account} region={REGION}")


def _s3_seed(c, env):
    """Sync data from demo seed buckets into the minelogx-{env}-* buckets."""
    for suffix, src in DEMO_SEED_BUCKETS.items():
        dst = f"{NAME_PREFIX}-{env}-{suffix}"
        print(f"==> seeding s3://{src}/ -> s3://{dst}/")
        c.run(f"aws s3 sync s3://{src}/ s3://{dst}/ --region {REGION}")


def _cfn_extra_params(env):
    """Load env-specific parameter overrides from params/<env>.json if it exists.

    Returns a string of 'Key=Value' pairs ready to append to --parameter-overrides,
    or an empty string when no file is found. Used to pass e.g. existing VPC IDs
    when the account is at the VPC limit.
    """
    import json as _json

    params_file = CFN_ROOT / "params" / f"{env}.json"
    if not params_file.exists():
        return ""
    data = _json.loads(params_file.read_text(encoding="utf-8"))
    return " ".join(f"{k}={v}" for k, v in data.items())


def _cfn(c, env, execute, build_pdf_layer=False, build_csv_layer=False):
    """Package + deploy the single parent stack (nested children) as minelogx-<env>.

    `package` uploads the child templates to S3 and rewrites TemplateURLs.
    execute=False creates a change set without applying (plan).
    """
    apn = _apn(env)
    fallback = "true" if env == "prod" else "false"
    pdf_layer = "true" if build_pdf_layer else "false"
    csv_layer = "true" if build_csv_layer else "false"
    extra = _cfn_extra_params(env)
    with c.cd(str(CFN_ROOT)):
        c.run(
            "aws cloudformation package --template-file parent.yaml "
            f"--s3-bucket {STATE_BUCKET} --s3-prefix cfn/{env} "
            f"--output-template-file packaged-parent.yaml --region {REGION}"
        )
        overrides = (
            f"NamePrefix={NAME_PREFIX}-{env} Environment={env} "
            f"ProjectApnId={apn} EnableLlmFallback={fallback} "
            f"BuildPdfLayer={pdf_layer} BuildCsvLayer={csv_layer}"
        )
        if extra:
            overrides += f" {extra}"
        cmd = (
            "aws cloudformation deploy --template-file packaged-parent.yaml "
            f"--stack-name {NAME_PREFIX}-{env} "
            f"--parameter-overrides {overrides} "
            f"--tags aws-apn-id={apn} Environment={env} ManagedBy=cloudformation "
            f"--capabilities CAPABILITY_NAMED_IAM --region {REGION}"
        )
        if not execute:
            cmd += " --no-execute-changeset"
        c.run(cmd)


# --------------------------------------------------------------------------- #
# env.* — environment orchestration
# --------------------------------------------------------------------------- #
def _up_log_path(env):
    """Return a timestamped log file path for a failed env.up run."""
    LOGS_DIR.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return LOGS_DIR / f"up-{env}-{ts}.log"


def _cfn_empty_s3_buckets(c, stack_name: str) -> None:
    """Empty all S3 buckets owned by a CFN stack so deletion doesn't fail."""
    import json as _json

    res = c.run(
        f"aws cloudformation list-stack-resources --stack-name {stack_name} "
        f"--region {REGION} --output json",
        warn=True,
        hide=True,
    )
    if not res or res.exited != 0:
        return
    try:
        resources = _json.loads(res.stdout).get("StackResourceSummaries", [])
    except Exception:
        return
    buckets = [
        r["PhysicalResourceId"]
        for r in resources
        if r.get("ResourceType") == "AWS::S3::Bucket" and r.get("PhysicalResourceId")
    ]
    # Also recurse into nested stacks
    nested = [
        r["PhysicalResourceId"]
        for r in resources
        if r.get("ResourceType") == "AWS::CloudFormation::Stack"
        and r.get("PhysicalResourceId")
    ]
    for nested_arn in nested:
        nested_name = nested_arn.split("/")[1] if "/" in nested_arn else nested_arn
        _cfn_empty_s3_buckets(c, nested_name)
    for bucket in buckets:
        print(f"    Emptying s3://{bucket} ...")
        c.run(
            f"aws s3 rm s3://{bucket} --recursive --region {REGION}",
            warn=True,
            hide=True,
        )


def _cfn_down(c, env):
    """Delete the CFN parent stack and wait until it is gone."""
    stack_name = f"{NAME_PREFIX}-{env}"
    print(f"==> down (auto-cleanup): deleting stack {stack_name} ...")
    _cfn_empty_s3_buckets(c, stack_name)
    c.run(
        f"aws cloudformation delete-stack --stack-name {stack_name} --region {REGION}",
        warn=True,
    )
    c.run(
        f"aws cloudformation wait stack-delete-complete --stack-name {stack_name} --region {REGION}",
        warn=True,
    )


@task(
    help={
        "env": "Environment name (dev|qa|prod|dev-<user>).",
        "engine": "terraform | cloudformation",
        "seed": "After deploy, sync data from demo buckets into the new buckets (dev only).",
        "no_rollback": "Skip automatic env.down on failure (keeps stack for manual inspection).",
        "skip_frontend": "Skip frontend build+deploy (infra only).",
    },
)
def up(
    c, env, engine="cloudformation", seed=False, no_rollback=False, skip_frontend=False
):
    """Create/update an environment. Builds Lambda layers, deploys infra, and deploys the frontend."""
    engine = _norm_engine(engine)
    _ensure_aws(c)
    print(f"==> up: env={env} engine={engine} region={REGION}")

    log_path = _up_log_path(env)

    try:
        # --- 1. Build Lambda layers (always, so the zip is ready for CFN package) ---
        for fn in ("csv", "pdf"):
            reqs = TARGET_ROOT / "backend" / f"requirements-{fn}.txt"
            if reqs.exists():
                print(f"==> building lambda layer: {fn}")
                build_layer(c, fn)
            else:
                print(f"[warn] {reqs.name} not found — skipping {fn} layer build.")

        # --- 2. Deploy infrastructure ---
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
                '-var="build_pdf_layer=true"',
                '-var="build_csv_layer=true"',
            )
        else:
            _cfn(c, env, execute=True, build_pdf_layer=True, build_csv_layer=True)

        # --- 3. Seed S3 (dev only) ---
        if seed:
            if env not in SEED_ALLOWED_ENVS:
                print(
                    f"[warn] --seed ignored for '{env}': only allowed in {SEED_ALLOWED_ENVS}."
                )
            else:
                _s3_seed(c, env)

        # --- 4. Endpoints summary (includes ApiUrl for frontend build) ---
        outputs = _endpoints_data(c, env)
        if outputs:
            _show_and_save_endpoints(env, outputs)
        else:
            print(
                "[warn] Sin outputs de CloudFormation — omitiendo resumen de endpoints."
            )

        # --- 5. Frontend build + deploy (inyecta VITE_API_BASE_URL dinámicamente) ---
        if not skip_frontend:
            api_url = next(
                (
                    o["OutputValue"]
                    for o in (outputs or [])
                    if o["OutputKey"] == "ApiUrl"
                ),
                "",
            )
            _frontend_build_and_deploy(c, env, api_url=api_url)

    except Exception as exc:
        msg = f"env.up failed: {exc}"
        log_path.write_text(msg, encoding="utf-8")
        print(f"\n[error] {msg}")
        print(f"[error] Full error saved to: {log_path}")
        if no_rollback:
            print("[warn] --no-rollback set: skipping automatic cleanup.")
        else:
            print("==> Running automatic env.down to clean up ...")
            _cfn_down(c, env)
        raise SystemExit(1) from None


@task(
    help={
        "env": "Environment name.",
        "engine": "terraform | cloudformation",
        "build_pdf_layer": "Attach the PDF deps layer (must run `fab lambda.build-layer pdf` first).",
        "build_csv_layer": "Attach the CSV deps layer (must run `fab lambda.build-layer csv` first).",
    },
)
def plan(c, env, engine="cloudformation", build_pdf_layer=False, build_csv_layer=False):
    """Preview changes without applying (terraform plan / CFN change set)."""
    engine = _norm_engine(engine)
    _ensure_aws(c)
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
            f'-var="build_pdf_layer={"true" if build_pdf_layer else "false"}"',
            f'-var="build_csv_layer={"true" if build_csv_layer else "false"}"',
        )
        return

    _cfn(
        c,
        env,
        execute=False,
        build_pdf_layer=build_pdf_layer,
        build_csv_layer=build_csv_layer,
    )


@task(
    help={"env": "Environment name.", "engine": "terraform | cloudformation"},
)
def down(c, env, engine="cloudformation"):
    """Destroy an environment. Guarded against fixed prod."""
    engine = _norm_engine(engine)
    _ensure_aws(c)
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
                c.run(f'"{TERRAFORM}" workspace select default', warn=True)
                c.run(f'"{TERRAFORM}" workspace delete {env}', warn=True)
        return

    # CloudFormation: empty S3 buckets first (CFN can't delete non-empty buckets).
    stack_name = f"{NAME_PREFIX}-{env}"
    _cfn_empty_s3_buckets(c, stack_name)
    c.run(
        f"aws cloudformation delete-stack --stack-name {stack_name} --region {REGION}",
        warn=True,
    )


@task
def list(c):
    """List active environments (Terraform workspaces + minelogx-* CFN stacks)."""
    _ensure_aws(c)
    print("== Terraform workspaces (ephemeral) ==")
    with c.cd(str(TF_ENVS / "ephemeral")):
        c.run(f'"{TERRAFORM}" workspace list', warn=True)
    print("\n== CloudFormation stacks (minelogx-*) ==")
    c.run(
        f"aws cloudformation list-stacks --region {REGION} "
        f"--stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE "
        f"--query \"StackSummaries[?starts_with(StackName, '{NAME_PREFIX}-')].StackName\" "
        f"--output table",
        warn=True,
    )


@task
def bootstrap(c):
    """Create the S3 bucket used by `cloudformation package` to upload nested templates.

    Run ONCE per account — dev/qa/prod share the same bucket under different S3 prefixes.
    Idempotent (skips what already exists). When PROD moves to its own account, run this
    there too. No DynamoDB needed — CloudFormation manages stack concurrency internally.
    """
    _ensure_aws(c)
    print(f"==> bootstrap: bucket={STATE_BUCKET} region={REGION}")

    if c.run(f"aws s3api head-bucket --bucket {STATE_BUCKET}", hide=True, warn=True).ok:
        print(f"    bucket {STATE_BUCKET} already exists")
    else:
        loc = (
            ""
            if REGION == "us-east-1"
            else f" --create-bucket-configuration LocationConstraint={REGION}"
        )
        c.run(f"aws s3api create-bucket --bucket {STATE_BUCKET} --region {REGION}{loc}")
        c.run(
            f"aws s3api put-bucket-versioning --bucket {STATE_BUCKET} "
            "--versioning-configuration Status=Enabled"
        )
        c.run(
            f"aws s3api put-public-access-block --bucket {STATE_BUCKET} "
            "--public-access-block-configuration "
            "BlockPublicAcls=true,IgnorePublicAcls=true,"
            "BlockPublicPolicy=true,RestrictPublicBuckets=true"
        )
    print(
        "Done. Next: uv run fab lambda.build-layer csv && fab lambda.build-layer pdf && fab env.up dev"
    )


# --------------------------------------------------------------------------- #
# ollama.* — demo EC2 remote ops (unchanged use case)
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
# lambda.* — scan/download the DEMO Lambda code (reference base, engine-agnostic)
# --------------------------------------------------------------------------- #
# Directory tree for the pulled demo code — deliberately OUTSIDE backend/ so it
# never mixes with the target-architecture code the team writes.
DEMO_LAMBDA_DIR = TARGET_ROOT / "demo" / "lambdas"

# Top-level entries that are almost certainly vendored deps (not our source).
# Used only to flag files for review before commit — nothing is auto-deleted.
_VENDORED_HINTS = {
    "boto3",
    "botocore",
    "s3transfer",
    "dateutil",
    "urllib3",
    "six",
    "jmespath",
    "certifi",
    "charset_normalizer",
    "idna",
    "requests",
    "numpy",
    "pandas",
    "pyarrow",
    "bin",
    "pydantic",
    "pydantic_core",
    "typing_extensions",
}


def _short_demo_name(name, pattern):
    """minelogx-lambda-ml-demo-poc -> ml (strip project prefix + demo suffix)."""
    short = name
    for pfx in (f"{NAME_PREFIX}-lambda-", f"{NAME_PREFIX}-"):
        if short.startswith(pfx):
            short = short[len(pfx) :]
            break
    if short.endswith(f"-{pattern}"):
        short = short[: -len(f"-{pattern}")]
    return short or name


@task(
    help={
        "pattern": "Substring identifying the demo functions (default: demo-poc).",
    },
)
def pull(c, pattern="demo-poc"):
    """Download the DEPLOYED demo Lambda code into onprem-aws/demo/lambdas/<fn>/.

    Engine-agnostic (uses `aws lambda get-function` + the presigned code URL, not
    Terraform/CloudFormation). Also dumps each function's live configuration to
    `._deployed-config.json` as a reference for wiring the target architecture.
    Vendored dependencies inside the zip are flagged for review before commit.
    """
    _ensure_aws(c)
    res = c.run(
        f"aws lambda list-functions --region {REGION} "
        '--query "Functions[].FunctionName" --output text',
        hide=True,
    )
    names = [n for n in res.stdout.split() if pattern in n]
    if not names:
        raise SystemExit(f"No Lambda functions matching '{pattern}' in {REGION}.")

    print(f"==> Found {len(names)} demo function(s): {', '.join(names)}")
    for name in names:
        short = _short_demo_name(name, pattern)
        target = DEMO_LAMBDA_DIR / short
        target.mkdir(parents=True, exist_ok=True)

        # Presigned URL to the deployment package, then download + extract in-process.
        loc = c.run(
            f"aws lambda get-function --function-name {name} --region {REGION} "
            '--query "Code.Location" --output text',
            hide=True,
        ).stdout.strip()
        print(f"    - {name} -> demo/lambdas/{short}/")
        # get-function always returns an https presigned S3 URL; validate the
        # scheme before opening it (defends against a file:/ or custom scheme).
        if not loc.lower().startswith("https://"):
            raise SystemExit(f"Refusing to download non-https code URL: {loc[:40]}...")
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            urllib.request.urlretrieve(loc, tmp_path)  # nosec B310 - https validated above
            with zipfile.ZipFile(tmp_path) as zf:
                zf.extractall(target)
        finally:
            os.unlink(tmp_path)

        # Live configuration (handler, runtime, memory, timeout, env vars, layers).
        cfg = c.run(
            f"aws lambda get-function-configuration --function-name {name} "
            f"--region {REGION}",
            hide=True,
        ).stdout
        (target / "._deployed-config.json").write_text(cfg, encoding="utf-8")

        # Flag likely-vendored top-level entries so they are reviewed before commit.
        # NOTE: `list` is shadowed by the env.list task in this module — use any().
        has_distinfo = any(target.glob("*.dist-info"))
        vendored = sorted(
            p.name
            for p in target.iterdir()
            if p.is_dir() and (p.name in _VENDORED_HINTS or has_distinfo)
        )
        own = sorted(p.name for p in target.glob("*.py"))
        print(f"        own source (commit): {own or '—'}")
        if vendored:
            print(f"        vendored (review/remove before commit): {vendored}")

    print(
        "\nDone. Review demo/lambdas/, remove vendored deps, then commit only your "
        "source. This is the reference base for the target-architecture api handler."
    )


# --------------------------------------------------------------------------- #
# lambda.build-layer — build a runtime-deps Lambda Layer without Docker
# --------------------------------------------------------------------------- #
# Layer build output consumed by modules/lambda_layer (Terraform) and the
# Lambda::LayerVersion resource (CloudFormation) — see requirements-<fn>.txt
# for which function gets which deps and why they're split per-function.
LAMBDA_LAYERS_DIR = TARGET_ROOT / ".lambda-layers"

# 250MB decompressed is the Lambda hard limit for code+layers combined; warn
# well before it since the deployment package itself adds a few more MB.
LAYER_SIZE_WARN_BYTES = 220 * 1024 * 1024


def _dir_size(path):
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


@task(
    help={
        "fn": "Function whose deps to build: pdf | csv (each has its own requirements-<fn>.txt).",
    },
)
def build_layer(c, fn):
    """Build a Lambda Layer's python/ tree via pip --platform (no Docker needed)."""
    reqs = TARGET_ROOT / "backend" / f"requirements-{fn}.txt"
    if not reqs.exists():
        raise SystemExit(
            f"No {reqs} — add it first (one requirements file per function)."
        )

    build_dir = LAMBDA_LAYERS_DIR / fn
    python_dir = build_dir / "python"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    python_dir.mkdir(parents=True)

    print(f"==> Building layer for '{fn}' from {reqs.name} -> {build_dir}")
    # uv venvs do not include pip by default — bootstrap it if missing.
    c.run(f'"{sys.executable}" -m ensurepip --upgrade', warn=True, hide=True)
    # manylinux2014 (glibc 2.17) — matches Amazon Linux 2, which backs the
    # Lambda python3.11 runtime (glibc 2.26). Newer manylinux_2_28 wheels
    # (glibc >=2.28, e.g. PyMuPDF >=1.26) would import-error at runtime — pin
    # deps to versions that still ship a manylinux2014 wheel.
    print("    Installing dependencies...", end="", flush=True)
    c.run(
        f'"{sys.executable}" -m pip install -q '
        "--platform manylinux2014_x86_64 --python-version 3.11 "
        "--implementation cp --abi cp311 --only-binary=:all: "
        f'--target "{python_dir}" -r "{reqs}"'
    )
    print(" done")

    size = _dir_size(build_dir)
    print(f"==> Layer '{fn}' built: {size / 1024 / 1024:.1f} MB decompressed")
    if size > LAYER_SIZE_WARN_BYTES:
        print(
            "    WARNING: over the safety margin for the 250MB Lambda code+layers "
            "limit — trim requirements or split further before deploying."
        )
    next_msg = (
        "Next: fab env.plan <env> --engine tf --build-pdf-layer (or --engine cf)."
        if fn == "pdf"
        else "Next: fab env.plan <env> --engine tf --build-csv-layer (or --engine cf)."
    )
    print(f"\n{next_msg}")


# --------------------------------------------------------------------------- #
# frontend.* — build React/Vite app and push to Amplify (manual deployment)
# --------------------------------------------------------------------------- #
FRONTEND_DIR = REPO_ROOT / "shared" / "frontend"
# Amplify branch used by all non-prod environments (prod deploys from main).
AMPLIFY_BRANCH = os.environ.get("AMPLIFY_BRANCH", "dev")


def _amplify_app_id(c, env):
    """Discover the Amplify app ID for the given environment from AWS.

    Looks for an app whose name matches `minelogx-<env>-frontend`, which is
    the name the Amplify TF module creates. Falls back to TF output if the
    app is not yet created.
    """
    app_name = f"{NAME_PREFIX}-{env}-frontend"
    result = c.run(
        f"aws amplify list-apps --region {REGION} "
        '--query "apps[?name==`' + app_name + '`].appId" --output text',
        hide=True,
        warn=True,
    )
    app_id = result.stdout.strip() if result.ok else ""
    if not app_id:
        raise SystemExit(
            f"Amplify app '{app_name}' not found in {REGION}. "
            "Run `fab env.up <env>` first to create the infrastructure."
        )
    return app_id


def _amplify_branch(c, app_id):
    """Return the first branch of the app (Amplify only has one branch per env)."""
    result = c.run(
        f"aws amplify list-branches --app-id {app_id} --region {REGION} "
        '--query "branches[0].branchName" --output text',
        hide=True,
    )
    branch = result.stdout.strip()
    if not branch or branch == "None":
        raise SystemExit(
            f"No branches found for Amplify app {app_id}. Run `fab env.up <env>` first."
        )
    return branch


def _amplify_upload_and_poll(c, env, dist_dir, log):
    """Zip dist/, upload to Amplify via presigned URL, poll until SUCCEED/FAILED.

    Returns the deployed URL on success; raises SystemExit on failure.
    This is the shared upload logic used by both frontend.deploy and env.up.
    """
    import json as _json
    import time as _time

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        zip_path = tmp.name
    try:
        log(f"==> [frontend] Zipping {dist_dir}")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in dist_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(dist_dir))

        app_id = _amplify_app_id(c, env)
        branch = _amplify_branch(c, app_id)
        log(f"==> [frontend] Amplify app={app_id} branch={branch}")

        create_result = c.run(
            f"aws amplify create-deployment --app-id {app_id} "
            f"--branch-name {branch} --region {REGION} --output json",
            hide=True,
        )
        deployment = _json.loads(create_result.stdout)
        job_id = deployment["jobId"]
        upload_url = deployment["zipUploadUrl"]
        log(f"==> [frontend] Job created: jobId={job_id}")

        urllib.request.urlopen(  # nosec B310 - presigned URL from AWS API
            urllib.request.Request(
                upload_url,
                data=open(zip_path, "rb").read(),  # noqa: WPS515
                method="PUT",
                headers={"Content-Type": "application/zip"},
            )
        )

        c.run(
            f"aws amplify start-deployment --app-id {app_id} "
            f"--branch-name {branch} --job-id {job_id} --region {REGION}",
            hide=True,
        )
        log("==> [frontend] Polling deployment status...")
        status = "PENDING"
        for _ in range(60):
            res = c.run(
                f"aws amplify get-job --app-id {app_id} "
                f"--branch-name {branch} --job-id {job_id} --region {REGION} "
                '--query "job.summary.status" --output text',
                hide=True,
            )
            status = res.stdout.strip()
            if status in ("SUCCEED", "FAILED", "CANCELLED"):
                break
            log(f"    status={status} — waiting 5s...")
            _time.sleep(5)

        url = f"https://{branch}.{app_id}.amplifyapp.com"
        if status == "SUCCEED":
            log(f"\n==> [frontend] DEPLOYED  status={status}\n    URL: {url}")
            return url
        raise SystemExit(f"Amplify deployment {status}. Check the console for logs.")
    finally:
        os.unlink(zip_path)


def _find_pnpm(c) -> str:
    """Locate pnpm cross-platform by querying the npm global prefix at runtime."""
    found = shutil.which("pnpm") or shutil.which("pnpm.cmd")
    if found:
        return found
    # npm global bin: on Windows the prefix dir itself contains the shims;
    # on POSIX it's prefix/bin. Querying npm at runtime avoids hardcoded paths.
    res = c.run("npm config get prefix", hide=True, warn=True)
    if res and res.exited == 0:
        prefix = Path(res.stdout.strip())
        candidates = [prefix / "pnpm.cmd", prefix / "pnpm", prefix / "bin" / "pnpm"]
        for p in candidates:
            if p.exists():
                return str(p)
    raise SystemExit("[frontend] pnpm not found — install with: npm install -g pnpm")


def _frontend_build_and_deploy(c, env, api_url=""):
    """Build the React/Vite app with a live API URL, then upload to Amplify.

    Injects VITE_API_BASE_URL dynamically so it always reflects the current
    stack — safe even after env.down + env.up (API GW ID changes).
    Writes a friendly log to .fab-logs/frontend-deploy-<env>-<ts>.log.
    """
    import time as _time

    ts = _time.strftime("%Y%m%d-%H%M%S")
    log_path = LOGS_DIR / f"frontend-deploy-{env}-{ts}.log"
    LOGS_DIR.mkdir(exist_ok=True)
    lines: list[str] = []

    def _log(msg: str) -> None:
        print(msg)
        lines.append(msg)

    _log(f"==> [frontend] build+deploy  env={env}  region={REGION}")
    _log(f"==> [frontend] VITE_API_BASE_URL={api_url or '(empty — will use mock)'}")

    build_env = {
        **os.environ,
        "VITE_API_BASE_URL": api_url,
        "VITE_USE_MOCK": "false" if api_url else "true",
    }

    pnpm = _find_pnpm(c)
    _log(f"==> [frontend] Building in {FRONTEND_DIR}")
    with c.cd(str(FRONTEND_DIR)):
        _log("==> [frontend] pnpm install")
        c.run(f'"{pnpm}" install --frozen-lockfile', env=build_env)
        _log("==> [frontend] pnpm type-check")
        c.run(f'"{pnpm}" type-check', env=build_env)
        _log("==> [frontend] pnpm build")
        c.run(f'"{pnpm}" build', env=build_env)

    dist_dir = FRONTEND_DIR / "dist"
    if not (dist_dir / "index.html").exists():
        raise SystemExit(f"[frontend] Build failed — {dist_dir}/index.html not found")
    _log("==> [frontend] Build OK")

    try:
        url = _amplify_upload_and_poll(c, env, dist_dir, _log)
        return url
    finally:
        log_path.write_text("\n".join(lines), encoding="utf-8")
        _log(f"==> [frontend] Log: {log_path}")


@task(
    help={
        "env": "Environment to deploy the frontend to (dev|qa|prod|dev-<user>).",
        "skip_build": "Skip pnpm build (use existing dist/ — useful for re-deploys).",
        "api_url": "Override VITE_API_BASE_URL. Defaults to the live stack output.",
    },
)
def deploy(c, env, skip_build=False, api_url=""):
    """Build the React/Vite frontend and push it to Amplify (manual deployment).

    Steps:
      1. Resolve VITE_API_BASE_URL from CFN stack outputs (or --api-url override)
      2. pnpm install + type-check + pnpm build  (skippable with --skip-build)
      3. Zip shared/frontend/dist/
      4. Create an Amplify manual deployment job (returns a presigned S3 upload URL)
      5. PUT the zip to the presigned URL
      6. Start the deployment job and poll until SUCCEED/FAILED
    """
    _ensure_aws(c)
    print(f"==> frontend.deploy: env={env} region={REGION}")

    # Resolve API URL: CLI flag → stack output → empty (mock)
    resolved_url = api_url
    if not resolved_url:
        outputs = _endpoints_data(c, env)
        resolved_url = next(
            (o["OutputValue"] for o in outputs if o["OutputKey"] == "ApiUrl"), ""
        )

    if skip_build:
        dist_dir = FRONTEND_DIR / "dist"
        if not dist_dir.exists():
            raise SystemExit(f"{dist_dir} not found. Run without --skip-build first.")
        _amplify_upload_and_poll(c, env, dist_dir, print)
    else:
        _frontend_build_and_deploy(c, env, api_url=resolved_url)


# --------------------------------------------------------------------------- #
# env.endpoints helpers
# --------------------------------------------------------------------------- #


def _endpoints_data(c, env):
    """Return CFN stack Outputs for env, or [] if the stack is not found."""
    import json as _json

    result = c.run(
        f"aws cloudformation describe-stacks --stack-name {NAME_PREFIX}-{env} "
        f'--region {REGION} --query "Stacks[0].Outputs" --output json',
        hide=True,
        warn=True,
    )
    if not result.ok or not result.stdout.strip() or result.stdout.strip() == "null":
        return []
    return _json.loads(result.stdout)


def _show_and_save_endpoints(env, outputs):
    """Pretty-print CFN outputs and write endpoints-<env>.md to the repo root."""
    import datetime as _dt
    from tabulate import tabulate as _tabulate

    rows = []
    for o in outputs:
        key = o["OutputKey"]
        val = o["OutputValue"]
        # Color URLs cyan
        display_val = _cyan(val) if val.startswith("http") else val
        rows.append([key, display_val])

    table = _tabulate(
        rows, headers=["Resource", "Value"], tablefmt="simple", disable_numparse=True
    )

    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  Endpoints — {NAME_PREFIX}-{env}  ({REGION})")
    print(sep)
    for line in table.splitlines():
        print(f"  {line}")
    print(f"{sep}\n")

    md_path = REPO_ROOT / f"endpoints-{env}.md"
    now = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Endpoints — `{NAME_PREFIX}-{env}`",
        "",
        f"> Generado: {now} | Región: `{REGION}`",
        "",
        "| Key | Value | Descripción |",
        "|---|---|---|",
    ]
    for o in outputs:
        key = o["OutputKey"]
        val = o["OutputValue"]
        desc = o.get("Description", "—")
        lines.append(f"| `{key}` | {val} | {desc} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Guardado → {md_path.name}")


@task(help={"env": "Environment name (dev|qa|prod|dev-<user>)."})
def endpoints(c, env):
    """Show and save all live endpoints for an environment.

    Reads CloudFormation stack Outputs and prints a friendly table.
    Also writes endpoints-<env>.md in the repo root (git-ignored).
    """
    _ensure_aws(c)
    outputs = _endpoints_data(c, env)
    if not outputs:
        raise SystemExit(
            f"  [!] Stack '{NAME_PREFIX}-{env}' no encontrado o sin outputs.\n"
            "      Ejecuta `uv run fab env.up <env>` primero."
        )
    _show_and_save_endpoints(env, outputs)


# --------------------------------------------------------------------------- #
# lambda.* — pipeline operations
# --------------------------------------------------------------------------- #
ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "586928288932")

# Logical names → Lambda function suffixes (minelogx-<env>-<suffix>)
LAMBDA_NAMES = ("api", "csv", "pdf")


def _lambda_fn(env, name):
    return f"{NAME_PREFIX}-{env}-{name}"


def _lambda_get_env(c, fn_name):
    """Return the current Lambda environment variables as a dict."""
    res = c.run(
        f"aws lambda get-function-configuration --function-name {fn_name} "
        f'--query "Environment.Variables" --output json --region {REGION}',
        hide=True,
        warn=True,
    )
    if not res.ok or res.stdout.strip() in ("", "null"):
        return {}
    return json.loads(res.stdout)


@task(
    help={
        "name": "Lambda suffix: api | csv | pdf.",
        "env": "Environment name (dev|qa|prod|dev-<user>).",
        "key": "Environment variable name to set.",
        "value": "New value for the variable.",
    },
)
def set_env(c, name, env, key, value):
    """Update a single environment variable on a deployed Lambda (non-destructive merge).

    Reads the current variables first, merges the new key=value, then writes
    back — existing variables are preserved. Use this to override model IDs,
    endpoints, or feature flags without a full env.up redeploy.

    Examples:
      uv run fab lambda.set-env pdf dev --key PDF_HAIKU_MODEL_ID --value us.anthropic.claude-3-haiku-20240307-v1:0
      uv run fab lambda.set-env api dev --key LOG_LEVEL --value DEBUG
    """
    _ensure_aws(c)
    fn_name = _lambda_fn(env, name)
    current = _lambda_get_env(c, fn_name)
    old_val = current.get(key, "<not set>")
    current[key] = value

    print("==> Updating Lambda env var")
    print(f"    function : {fn_name}")
    print(f"    {key}  {old_val}  ->  {value}")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump({"Variables": current}, tmp)
        tmp_path = tmp.name

    try:
        c.run(
            f"aws lambda update-function-configuration "
            f"--function-name {fn_name} "
            f"--environment file://{tmp_path} "
            f"--region {REGION} --output json",
            hide=True,
        )
    finally:
        os.unlink(tmp_path)

    print(f"==> Done. Lambda {fn_name} updated (change is live in ~5 s).")


@task(
    help={
        "name": "Lambda suffix: api | csv | pdf. Omit to show all three.",
        "env": "Environment name (dev|qa|prod|dev-<user>).",
        "follow": "Keep tailing (Ctrl-C to stop). Default: last 50 lines and exit.",
    },
)
def lambda_logs(c, name, env, follow=False):
    """Tail CloudWatch logs for a Lambda function.

    Examples:
      uv run fab lambda.logs pdf dev
      uv run fab lambda.logs pdf dev --follow
    """
    _ensure_aws(c)
    fn_name = _lambda_fn(env, name)
    log_group = f"/aws/lambda/{fn_name}"
    follow_flag = "--follow" if follow else ""
    print(f"==> Logs for {fn_name}  ({log_group})")
    c.run(
        f"aws logs tail {log_group} --since 1h {follow_flag} --region {REGION}",
        warn=True,
    )


@task(
    help={"env": "Environment name (dev|qa|prod|dev-<user>)."},
)
def lambda_status(c, env):
    """Show runtime config for all three Lambda functions in an environment.

    Displays function state, runtime, memory, timeout, last modified, and
    every environment variable currently set on each function.
    """
    _ensure_aws(c)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    lines = [
        "",
        _SEP,
        f"  Lambda Status — {NAME_PREFIX}-{env}  ({ts})",
        _SEP,
    ]
    for name in LAMBDA_NAMES:
        fn_name = _lambda_fn(env, name)
        res = c.run(
            f"aws lambda get-function-configuration --function-name {fn_name} "
            f"--region {REGION} --output json",
            hide=True,
            warn=True,
        )
        if not res.ok:
            lines.append(f"\n  [{fn_name}]  NOT FOUND")
            continue
        cfg = json.loads(res.stdout)
        state = cfg.get("State", "?")
        runtime = cfg.get("Runtime", "?")
        memory = cfg.get("MemorySize", "?")
        timeout = cfg.get("Timeout", "?")
        modified = cfg.get("LastModified", "?")[:19].replace("T", " ")
        env_vars = cfg.get("Environment", {}).get("Variables", {})

        lines += [
            f"\n  [{fn_name}]",
            f"  State        : {state}",
            f"  Runtime      : {runtime}    Memory: {memory} MB    Timeout: {timeout} s",
            f"  Last deploy  : {modified}",
            f"  Env vars ({len(env_vars)}):",
        ]
        for k, v in sorted(env_vars.items()):
            # Truncate long values (e.g. ARNs) for readability
            display = v if len(v) <= 60 else v[:57] + "..."
            lines.append(f"    {k:<35} {display}")

    lines.append(f"\n{_SEP}\n")
    print("\n".join(lines))
    log_path = _log_write(f"lambda-status-{env}", lines)
    print(f"  Log saved → {log_path.relative_to(REPO_ROOT)}")


@task(
    help={
        "pipeline": "Pipeline to invoke: csv | pdf.",
        "env": "Environment name (dev|qa|prod|dev-<user>). Default: dev.",
        "file_path": (
            "For csv: S3 key relative to the telemetry bucket "
            "(e.g. C1/fuel_management_events.csv). "
            "For pdf: S3 key relative to the legislation bucket "
            "(e.g. docs/test.pdf)."
        ),
        "force": "For csv: re-ingest even if the file was already processed.",
        "wait": "Block until the execution completes and print the final status.",
        "async_": "For pdf: fire-and-forget (InvocationType=Event). Returns 202 immediately; Lambda processes in background. Use for large PDFs that exceed the CLI read timeout.",
    },
)
def invoke(c, pipeline, env, file_path=None, force=False, wait=False, async_=False):
    """Invoke the csv or pdf pipeline manually without waiting for the scheduler.

    csv — starts a Step Functions execution with the given S3 key from the
          telemetry bucket.  Lists available files and picks the first one
          when --file-path is omitted.

    pdf — invokes Lambda PDF directly with a synthetic EventBridge S3 event
          built from the given key in the legislation bucket.  Lists available
          PDFs and picks the first one when --file-path is omitted.
    """
    import json as _json
    import time as _time

    _ensure_aws(c)

    if pipeline == "csv":
        telemetry_bucket = f"{NAME_PREFIX}-{env}-telemetry-data"

        if not file_path:
            res = c.run(
                f"aws s3 ls s3://{telemetry_bucket}/ --recursive "
                '--query "Contents[].Key" --output text',
                hide=True,
                warn=True,
            )
            keys = (
                [k for k in res.stdout.split() if k.endswith(".csv")] if res.ok else []
            )
            if not keys:
                raise SystemExit(
                    f"No CSV files found in s3://{telemetry_bucket}/. "
                    "Run `uv run fab env.up dev --seed` first."
                )
            file_path = keys[0]
            print(f"==> No --file-path given — using first CSV found: {file_path}")

        sm_arn = (
            f"arn:aws:states:{REGION}:{ACCOUNT_ID}:stateMachine:"
            f"{NAME_PREFIX}-{env}-csv-pipeline"
        )
        payload = _json.dumps({"file_path": file_path, "force": force})
        print("==> Starting Step Functions execution")
        print(f"    state machine : {sm_arn}")
        print(f"    input         : {payload}")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(payload)
            tmp_path = tmp.name

        try:
            res = c.run(
                f"aws stepfunctions start-execution "
                f"--state-machine-arn {sm_arn} "
                f"--input file://{tmp_path} "
                f"--region {REGION} --output json",
            )
        finally:
            os.unlink(tmp_path)

        execution = _json.loads(res.stdout)
        exec_arn = execution["executionArn"]
        print(f"==> Execution started: {exec_arn}")

        t_start = _time.monotonic()
        if wait:
            print("==> Waiting for execution to complete (polling every 15 s)...")
            for _ in range(80):  # up to 20 min
                status_res = c.run(
                    f"aws stepfunctions describe-execution "
                    f"--execution-arn {exec_arn} "
                    f'--query "status" --output text --region {REGION}',
                    hide=True,
                )
                status = status_res.stdout.strip()
                if status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
                    break
                print(f"    status={status} — waiting 15 s...")
                _time.sleep(15)
            elapsed = _time.monotonic() - t_start
            print(f"==> Final status: {status}")
            _log_invoke_csv(env, file_path, exec_arn, status, elapsed)
            if status != "SUCCEEDED":
                raise SystemExit(
                    f"Execution {status}. Check CloudWatch logs for details:\n"
                    f"  aws logs tail /aws/states/{NAME_PREFIX}-{env}-csv-pipeline "
                    f"--follow --region {REGION}"
                )
        else:
            _log_invoke_csv(env, file_path, exec_arn, "RUNNING (background)", 0)
            print(
                "==> Execution running in background. To check status:\n"
                f"    aws stepfunctions describe-execution "
                f"--execution-arn {exec_arn} "
                f"--query status --output text --region {REGION}"
            )

    elif pipeline == "pdf":
        legislation_bucket = f"{NAME_PREFIX}-{env}-legislation-documents"
        lambda_name = f"{NAME_PREFIX}-{env}-pdf"

        if not file_path:
            res = c.run(
                f"aws s3 ls s3://{legislation_bucket}/ --recursive "
                '--query "Contents[].Key" --output text',
                hide=True,
                warn=True,
            )
            keys = (
                [k for k in res.stdout.split() if k.lower().endswith(".pdf")]
                if res.ok
                else []
            )
            if not keys:
                raise SystemExit(
                    f"No PDF files found in s3://{legislation_bucket}/. "
                    "Upload a PDF or run `uv run fab env.up dev --seed` first."
                )
            file_path = keys[0]
            print(f"==> No --file-path given — using first PDF found: {file_path}")

        import urllib.parse as _urlparse

        event = {
            "detail": {
                "bucket": {"name": legislation_bucket},
                "object": {"key": _urlparse.quote(file_path, safe="")},
            }
        }
        payload = _json.dumps(event)
        invocation_type = "Event" if async_ else "RequestResponse"
        print("==> Invoking Lambda PDF directly")
        print(f"    function         : {lambda_name}")
        print(f"    bucket           : {legislation_bucket}")
        print(f"    key              : {file_path}")
        print(f"    invocation_type  : {invocation_type}")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(payload)
            payload_path = tmp.name

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as out:
            out_path = out.name

        try:
            extra = "--log-type Tail" if not async_ else "--invocation-type Event"
            res = c.run(
                f"aws lambda invoke "
                f"--function-name {lambda_name} "
                f"--payload file://{payload_path} "
                f"--cli-binary-format raw-in-base64-out "
                f"--region {REGION} "
                f"{extra} "
                f"{out_path}",
                warn=True,
            )
            if not async_:
                response = Path(out_path).read_text(encoding="utf-8")
                print(f"==> Response: {response}")
            else:
                print("==> Fired async (202). Lambda processing in background.")
        finally:
            os.unlink(payload_path)
            os.unlink(out_path)

        status_code = "202" if async_ else ("200" if res.ok else "ERROR")
        _log_invoke_pdf(env, file_path, lambda_name, status_code)
        if res.ok:
            print(
                "==> Check OpenSearch index pdf_legal_vecs or CloudWatch logs:\n"
                f"    aws logs tail /aws/lambda/{lambda_name} --follow --region {REGION}"
            )
        else:
            raise SystemExit("Lambda invoke failed. See output above for details.")

    else:
        raise SystemExit(f"Unknown pipeline '{pipeline}'. Use: csv | pdf")


@task(
    positional=["pipeline", "env"],
    help={
        "pipeline": "csv or pdf",
        "env": "Deployment environment (e.g. dev).",
        "force": "For csv: re-ingest even if already processed.",
        "parallel": "Launch all executions concurrently instead of waiting for each one (csv only).",
        "async_": "For pdf: fire-and-forget all files (InvocationType=Event). Returns 202 immediately per file. Use for large PDFs that exceed the CLI read timeout.",
        "verbose": "Print the full JSON response for each PDF invocation (pdf pipeline only).",
    },
)
def invoke_all(
    c, pipeline, env, force=False, parallel=False, async_=False, verbose=False
):
    """Invoke the csv or pdf pipeline for EVERY file in the S3 bucket.

    csv — starts one Step Functions execution per CSV key found in the
          telemetry bucket and waits for each to complete (unless --parallel).

    pdf — invokes Lambda PDF for each PDF key. Use --async to fire all without
          waiting (recommended for large PDFs that exceed the CLI read timeout).
    """
    import json as _json
    import time as _time

    _ensure_aws(c)

    if pipeline == "csv":
        telemetry_bucket = f"{NAME_PREFIX}-{env}-telemetry-data"
        res = c.run(
            f"aws s3 ls s3://{telemetry_bucket}/ --recursive "
            '--query "Contents[].Key" --output text',
            hide=True,
            warn=True,
        )
        keys = [k for k in res.stdout.split() if k.endswith(".csv")] if res.ok else []
        if not keys:
            raise SystemExit(
                f"No CSV files found in s3://{telemetry_bucket}/. "
                "Run `uv run fab env.up dev --seed` first."
            )

        sm_arn = (
            f"arn:aws:states:{REGION}:{ACCOUNT_ID}:stateMachine:"
            f"{NAME_PREFIX}-{env}-csv-pipeline"
        )
        print(
            f"==> Found {len(keys)} CSV file(s) — {'parallel' if parallel else 'serial'} mode"
        )

        exec_arns: list[tuple[str, str]] = []
        for key in keys:
            payload = _json.dumps({"file_path": key, "force": force})
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(payload)
                tmp_path = tmp.name
            try:
                r = c.run(
                    f"aws stepfunctions start-execution "
                    f"--state-machine-arn {sm_arn} "
                    f"--input file://{tmp_path} "
                    f"--region {REGION} --output json",
                )
            finally:
                os.unlink(tmp_path)
            exec_arn = _json.loads(r.stdout)["executionArn"]
            exec_arns.append((key, exec_arn))
            print(f"    started: {key}")
            if not parallel:
                # wait inline before moving to next file
                status = "RUNNING"
                t0 = _time.monotonic()
                for _ in range(80):
                    sr = c.run(
                        f"aws stepfunctions describe-execution "
                        f"--execution-arn {exec_arn} "
                        f'--query "status" --output text --region {REGION}',
                        hide=True,
                    )
                    status = sr.stdout.strip()
                    if status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
                        break
                    _time.sleep(15)
                elapsed = _time.monotonic() - t0
                _log_invoke_csv(env, key, exec_arn, status, elapsed)
                icon = _green("✓") if status == "SUCCEEDED" else _red("✗")
                status_label = _green(status) if status == "SUCCEEDED" else _red(status)
                print(f"    {icon} {status_label} {_dim(f'({elapsed:.0f}s)')} — {key}")
                if status != "SUCCEEDED":
                    print(
                        f"      Check: aws logs tail /aws/states/{NAME_PREFIX}-{env}-csv-pipeline --follow --region {REGION}"
                    )

        if parallel:
            print(
                f"\n==> All {len(exec_arns)} executions launched. Polling until done..."
            )
            pending = [*exec_arns]
            while pending:
                still = []
                for key, exec_arn in pending:
                    sr = c.run(
                        f"aws stepfunctions describe-execution "
                        f"--execution-arn {exec_arn} "
                        f'--query "status" --output text --region {REGION}',
                        hide=True,
                    )
                    status = sr.stdout.strip()
                    if status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
                        icon = _green("✓") if status == "SUCCEEDED" else _red("✗")
                        label = (
                            _green(status) if status == "SUCCEEDED" else _red(status)
                        )
                        print(f"    {icon} {label} — {key}")
                    else:
                        still.append((key, exec_arn))
                pending = still
                if pending:
                    print(
                        _yellow(f"    {len(pending)} still running")
                        + " — polling in 15 s..."
                    )
                    _time.sleep(15)
            print(_green("==> All executions finished."))

    elif pipeline == "pdf":
        import urllib.parse as _urlparse

        legislation_bucket = f"{NAME_PREFIX}-{env}-legislation-documents"
        lambda_name = f"{NAME_PREFIX}-{env}-pdf"

        res = c.run(
            f"aws s3 ls s3://{legislation_bucket}/ --recursive "
            '--query "Contents[].Key" --output text',
            hide=True,
            warn=True,
        )
        keys = (
            [k for k in res.stdout.split() if k.lower().endswith(".pdf")]
            if res.ok
            else []
        )
        if not keys:
            raise SystemExit(
                f"No PDF files found in s3://{legislation_bucket}/. "
                "Upload PDFs or run `uv run fab env.up dev --seed` first."
            )

        mode_label = "async (fire-and-forget)" if async_ else "sequential"
        print(f"==> Found {len(keys)} PDF file(s) — invoking {mode_label}")
        failed = []
        for key in keys:
            event = {
                "detail": {
                    "bucket": {"name": legislation_bucket},
                    "object": {"key": _urlparse.quote(key, safe="")},
                }
            }
            payload = _json.dumps(event)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(payload)
                payload_path = tmp.name
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as out:
                out_path = out.name
            try:
                extra = "--invocation-type Event" if async_ else "--log-type Tail"
                r = c.run(
                    f"aws lambda invoke "
                    f"--function-name {lambda_name} "
                    f"--payload file://{payload_path} "
                    f"--cli-binary-format raw-in-base64-out "
                    f"--region {REGION} "
                    f"{extra} "
                    f"{out_path}",
                    warn=True,
                )
                icon = _green("✓") if r.ok else _red("✗")
                suffix = _dim(" (async)") if async_ else ""
                print(f"    {icon} {key}{suffix}")
                if not async_ and verbose and r.ok:
                    try:
                        response_text = Path(out_path).read_text(encoding="utf-8")
                        print(_dim(f"      response: {response_text}"))
                    except Exception:
                        pass
                if not r.ok:
                    failed.append(key)
                status = "202" if (async_ and r.ok) else ("200" if r.ok else "ERROR")
                _log_invoke_pdf(env, key, lambda_name, status)
            finally:
                os.unlink(payload_path)
                os.unlink(out_path)

        if failed:
            print(f"\n==> {_red(str(len(failed)) + ' file(s) failed:')}")
            for f in failed:
                print(f"      {_yellow(f)}")
            print(
                _dim(
                    f"    Check: aws logs tail /aws/lambda/{lambda_name} --follow --region {REGION}"
                )
            )
        else:
            if async_:
                print(
                    _green(f"\n==> All {len(keys)} PDF(s) fired async.")
                    + _dim(" Lambda processing in background.")
                )
            else:
                print(_green(f"\n==> All {len(keys)} PDF(s) processed successfully."))
            print(
                _dim(f"    Monitor: uv run fab lambda.pdf-async-status {env} --follow")
            )

    else:
        raise SystemExit(f"Unknown pipeline '{pipeline}'. Use: csv | pdf")


# --------------------------------------------------------------------------- #
# Activity logging helpers
# --------------------------------------------------------------------------- #
_SEP = "─" * 64


def _log_write(name: str, lines: "list[str]") -> Path:
    """Write formatted activity lines to .fab-logs/<name>-<ts>.log and return the path."""
    LOGS_DIR.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = LOGS_DIR / f"{name}-{ts}.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _log_invoke_csv(env, file_path, exec_arn, status, elapsed_s):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    lines = [
        _SEP,
        f"  lambda.invoke csv  —  {ts}",
        _SEP,
        f"  env          : {env}",
        f"  file_path    : {file_path}",
        f"  execution    : {exec_arn}",
        f"  status       : {status}",
        f"  elapsed      : {elapsed_s:.0f} s",
        _SEP,
    ]
    path = _log_write(f"invoke-csv-{env}", lines)
    print("\n".join(lines))
    print(f"  Log saved → {path.relative_to(REPO_ROOT)}")


def _log_invoke_pdf(env, file_path, lambda_name, status_code):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    lines = [
        _SEP,
        f"  lambda.invoke pdf  —  {ts}",
        _SEP,
        f"  env          : {env}",
        f"  file_path    : {file_path}",
        f"  lambda       : {lambda_name}",
        f"  status_code  : {status_code}",
        _SEP,
    ]
    path = _log_write(f"invoke-pdf-{env}", lines)
    print("\n".join(lines))
    print(f"  Log saved → {path.relative_to(REPO_ROOT)}")


# --------------------------------------------------------------------------- #
# bedrock.* — model access status
# --------------------------------------------------------------------------- #

# Models the project uses, grouped by pipeline.
_BEDROCK_MODELS = {
    # RAG Agent + CSV pipeline — user-selectable via UI (key → env var → model id)
    "Lambda API / RAG Agent (selectable)": [
        "us.anthropic.claude-sonnet-4-6",  # RAG_CLAUDE_MODEL_ID  (default)
        "us.amazon.nova-pro-v1:0",  # RAG_NOVA_MODEL_ID
        "deepseek.v3.2",  # RAG_DEEPSEEK_MODEL_ID
    ],
    # CSV pipeline annotation
    "Lambda CSV (annotation + chunking)": [
        "us.anthropic.claude-sonnet-4-6",  # BEDROCK_MODEL_ID
    ],
    # CSV pipeline embeddings
    "Lambda CSV (embeddings 1024d)": [
        "cohere.embed-multilingual-v3",  # BEDROCK_EMBED_MODEL_ID
    ],
    # PDF pipeline classification
    "Lambda PDF (classification — Haiku fallback: Sonnet)": [
        "us.anthropic.claude-sonnet-4-6",  # PDF_HAIKU_MODEL_ID (Haiku 4.5 pending Marketplace sub)
    ],
    # PDF pipeline extraction
    "Lambda PDF (extraction — Sonnet)": [
        "us.anthropic.claude-sonnet-4-6",  # PDF_CLAUDE_MODEL_ID
    ],
    # PDF pipeline embeddings
    "Lambda PDF (embeddings 1536d — Titan)": [
        "amazon.titan-embed-text-v2:0",  # PDF_TITAN_MODEL_ID
    ],
}

# Minimal converse payload to probe text-generation models.
_PROBE_CONVERSE_MESSAGES = [{"role": "user", "content": [{"text": "hi"}]}]

# Minimal embed payload to probe embedding models.
_PROBE_EMBED_COHERE = {"texts": ["hi"], "input_type": "search_query"}
_PROBE_EMBED_TITAN = {"inputText": "hi"}


def _bedrock_probe(model_id: str) -> tuple[bool, str]:
    """Try a minimal invoke against a Bedrock model. Returns (ok, message)."""
    import boto3
    import botocore.exceptions

    client = boto3.client("bedrock-runtime", region_name=REGION)
    try:
        if "embed" in model_id:
            body = _PROBE_EMBED_TITAN if "titan" in model_id else _PROBE_EMBED_COHERE
            client.invoke_model(
                modelId=model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
        else:
            client.converse(
                modelId=model_id,
                messages=_PROBE_CONVERSE_MESSAGES,
                inferenceConfig={"maxTokens": 1},
            )
        return True, "GRANTED"
    except botocore.exceptions.ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("AccessDeniedException",):
            return False, "DENIED — not subscribed in Marketplace"
        if code in ("ValidationException",):
            return False, f"DENIED — {exc.response['Error']['Message'][:60]}"
        return False, f"ERROR ({code})"
    except Exception as exc:  # noqa: BLE001
        return False, f"ERROR — {exc}"


@task(help={"verbose": "Add a Notes column with extra info per model."})
def model_access(c, verbose=False):
    """Check Bedrock model access for every model this project uses.

    Probes each model with a minimal invoke to confirm real runtime access,
    not just IAM permissions. Models listed in CLAUDE.md under 'Bedrock Models'.
    If a model shows DENIED, enable it in the AWS Console:
      Bedrock -> Model access -> Manage model access -> select -> Save changes.
    """
    from tabulate import tabulate as _tabulate

    _ensure_aws(c)
    ts = datetime.datetime.now().isoformat(timespec="seconds")

    all_ok = True
    cache: dict[str, tuple[bool, str]] = {}
    rows = []
    for pipeline, models in _BEDROCK_MODELS.items():
        for model_id in models:
            if model_id not in cache:
                cache[model_id] = _bedrock_probe(model_id)
            ok, msg = cache[model_id]
            if not ok:
                all_ok = False
            if verbose:
                rows.append([model_id, pipeline, msg])
            else:
                rows.append([model_id, pipeline, msg])

    headers = (
        ["Model", "Pipeline", "Status"]
        if not verbose
        else ["Model", "Pipeline", "Status"]
    )
    table = _tabulate(rows, headers=headers, tablefmt="simple", disable_numparse=True)

    # Colorize status words in the rendered table
    table = table.replace("GRANTED", _green("GRANTED"))
    table = table.replace("DENIED", _red("DENIED"))
    table = table.replace("ERROR", _red("ERROR"))

    print()
    print(_bold(f"  Bedrock Model Access — account 586928288932  ({ts})"))
    print()
    # Indent each line
    for line in table.splitlines():
        print(f"  {line}")
    print()
    print("  To enable a denied model:")
    print("    AWS Console → Bedrock → Model access → Manage model access")
    print()

    plain_lines = [
        "",
        _SEP,
        f"  Bedrock Model Access — account 586928288932  ({ts})",
        _SEP,
        *[
            f"  {line}"
            for line in _tabulate(
                rows, headers=headers, tablefmt="simple", disable_numparse=True
            ).splitlines()
        ],
        "",
        "  To enable a denied model:",
        "    AWS Console → Bedrock → Model access → Manage model access",
        _SEP,
        "",
    ]
    log_path = _log_write("bedrock-model-access", plain_lines)
    print(f"  Log saved → {log_path.relative_to(REPO_ROOT)}")
    if not all_ok:
        raise SystemExit(1)


# --------------------------------------------------------------------------- #
# opensearch.* — collection status and index doc counts
# --------------------------------------------------------------------------- #
OPENSEARCH_INDICES = ["csv_telemetry_vecs", "pdf_legal_vecs"]


def _aoss_get(endpoint: str, path: str) -> dict:
    """SigV4-signed GET against an AOSS endpoint. Requires boto3 in the venv."""
    import boto3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    session = boto3.Session()
    creds: Credentials = session.get_credentials().get_frozen_credentials()
    url = endpoint.rstrip("/") + path
    aws_req = AWSRequest(method="GET", url=url)
    SigV4Auth(creds, "aoss", REGION).add_auth(aws_req)
    req = urllib.request.Request(url, headers=dict(aws_req.headers), method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
        return json.loads(resp.read())


@task(help={"env": "Environment name (dev|qa|prod|dev-<user>)."})
def opensearch_status(c, env):
    """Show OpenSearch Serverless collection status and document count per index.

    Reads the collection endpoint from CloudFormation outputs and queries each
    index via a SigV4-signed HTTP request (requires boto3 in the venv).
    """
    _ensure_aws(c)

    # --- 1. Collection info from AWS CLI ---
    col_name = f"{NAME_PREFIX}-{env}-vectors"
    res = c.run(
        f"aws opensearchserverless list-collections --region {REGION} "
        f"--collection-filters name={col_name} --output json",
        hide=True,
        warn=True,
    )
    collections = (
        json.loads(res.stdout).get("collectionSummaries", []) if res.ok else []
    )

    # --- 2. Endpoint from CFN outputs (already in endpoints-dev.md) ---
    outputs = _endpoints_data(c, env)
    endpoint = next(
        (o["OutputValue"] for o in outputs if o["OutputKey"] == "OpenSearchEndpoint"),
        None,
    )

    ts = datetime.datetime.now().isoformat(timespec="seconds")
    lines = [
        "",
        _SEP,
        f"  OpenSearch Serverless — {NAME_PREFIX}-{env}  ({ts})",
        _SEP,
    ]

    if collections:
        col = collections[0]
        lines += [
            f"  Collection   : {col.get('name')}",
            f"  Status       : {col.get('status')}",
            f"  Type         : {col.get('type')}",
            f"  ARN          : {col.get('arn')}",
            f"  Endpoint     : {endpoint or '—'}",
            "",
        ]
    else:
        lines += [f"  Collection '{col_name}' not found or no access.", ""]

    # --- 3. Doc counts per index (SigV4 to AOSS HTTP API) ---
    if endpoint:
        from tabulate import tabulate as _tabulate

        idx_rows = []
        for idx in OPENSEARCH_INDICES:
            try:
                data = _aoss_get(endpoint, f"/{idx}/_count")
                count = (
                    f"{data.get('count', '?'):,}"
                    if isinstance(data.get("count"), int)
                    else str(data.get("count", "?"))
                )
                idx_rows.append([idx, count, "ACTIVE"])
            except Exception as exc:  # noqa: BLE001
                idx_rows.append([idx, "—", f"ERROR: {exc}"])

        table = _tabulate(
            idx_rows,
            headers=["Index", "Docs", "Status"],
            tablefmt="simple",
            disable_numparse=True,
        )
        table = table.replace("ACTIVE", _green("ACTIVE")).replace(
            "ERROR", _red("ERROR")
        )
        for line in table.splitlines():
            lines.append(f"  {line}")
    else:
        lines.append("  (endpoint not available — run `fab env.up dev` first)")

    lines.append(_SEP)
    lines.append("")
    print("\n".join(lines))

    log_path = _log_write(f"opensearch-status-{env}", lines)
    print(f"  Log saved → {log_path.relative_to(REPO_ROOT)}")


@task(
    positional=["env"],
    help={
        "env": "Deployment environment (e.g. dev).",
        "last": "How many minutes back to look (default: 60).",
        "follow": "Keep polling every 15 s until interrupted (Ctrl+C).",
        "verbose": "Show full error messages and raw Lambda duration/memory stats.",
    },
)
def pdf_async_status(c, env, last=60, follow=False, verbose=False):
    """Show status of async PDF Lambda invocations (fired with --async flag).

    Queries CloudWatch Logs Insights for result summaries from the PDF Lambda
    log group, giving a per-file view of what finished, what errored, and what
    is still in-flight.
    """
    import json as _json
    import time as _time

    _ensure_aws(c)

    lambda_name = f"{NAME_PREFIX}-{env}-pdf"
    log_group = f"/aws/lambda/{lambda_name}"

    # Timestamps computed in Python — avoids $(date ...) portability issues on Windows
    def _timestamps():
        now = int(_time.time())
        return now - last * 60, now

    def _run_query(query_str):
        t_start, t_end = _timestamps()
        # Write query to temp file to avoid shell quoting issues on Windows
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as qtmp:
            qtmp.write(query_str)
            qtmp_path = qtmp.name
        try:
            res = c.run(
                f"aws logs start-query "
                f"--log-group-name {log_group} "
                f"--start-time {t_start} "
                f"--end-time {t_end} "
                f"--query-string file://{qtmp_path} "
                f"--region {REGION} --output json",
                hide=True,
                warn=True,
            )
        finally:
            os.unlink(qtmp_path)
        if not res.ok:
            return None
        query_id = _json.loads(res.stdout).get("queryId")
        if not query_id:
            return None
        for _ in range(30):
            r = c.run(
                f"aws logs get-query-results --query-id {query_id} "
                f"--region {REGION} --output json",
                hide=True,
            )
            data = _json.loads(r.stdout)
            if data.get("status") in ("Complete", "Failed", "Cancelled"):
                return data.get("results", [])
            _time.sleep(1)
        return None

    def _parse(results):
        # Group rows by RequestId to correlate errors with REPORT lines
        by_req: dict[str, list[dict]] = {}
        for row in results:
            flds = {f["field"]: f["value"] for f in row}
            msg = flds.get("@message", "")
            ts = flds.get("@timestamp", "")[:19]
            req = flds.get("@requestId", "") or ""
            # Extract requestId from Lambda log prefix if not a direct field
            if not req and "\t" in msg:
                parts = msg.split("\t")
                if len(parts) >= 2:
                    req = parts[1].strip()
            by_req.setdefault(req, []).append({"msg": msg, "ts": ts})

        invocations: list[dict] = []
        for req, rows in by_req.items():
            msgs = [r["msg"] for r in rows]
            ts = rows[0]["ts"]
            has_error = any("[ERROR]" in m for m in msgs)
            no_sections = any("No sections extracted" in m for m in msgs)
            # Try to extract file_key from any JSON payload in logs
            file_key = None
            sections = pages = duration = None
            for m in msgs:
                if "file_key" in m and "sections_indexed" in m:
                    try:
                        j = m[m.find("{") :]
                        outer = _json.loads(j)
                        inner = (
                            _json.loads(outer.get("body", "{}"))
                            if "body" in outer
                            else outer
                        )
                        file_key = inner.get("file_key")
                        sections = inner.get("sections_indexed")
                        pages = inner.get("total_pages")
                        duration = inner.get("duration_s")
                    except Exception:
                        pass
                # REPORT line — extract billed duration
                if "REPORT RequestId" in m:
                    for part in m.split("\t"):
                        if "Duration:" in part and "Billed" not in part:
                            try:
                                duration = (
                                    float(part.split(":")[1].strip().split()[0]) / 1000
                                )
                            except Exception:
                                pass

            # Collect meaningful error snippets
            error_snippets = []
            for m in msgs:
                if "[ERROR]" in m:
                    snippet = m.strip()
                    # Find the actual error message after the Lambda log prefix
                    if "\t" in snippet:
                        snippet = snippet.split("\t")[-1]
                    error_snippets.append(snippet[:140])

            status = "error" if (has_error or no_sections) else "ok"
            invocations.append(
                {
                    "req": req,
                    "ts": ts,
                    "file_key": file_key,
                    "status": status,
                    "sections": sections,
                    "pages": pages,
                    "duration": duration,
                    "errors": error_snippets,
                }
            )

        return sorted(invocations, key=lambda x: x["ts"])

    def _print_summary(invocations):
        print()
        print(
            f"  PDF async status — {_bold(lambda_name)}  {_dim(f'(last {last} min)')}"
        )
        print(_dim(_SEP))

        if not invocations:
            print(_yellow("  No invocations found in this window."))
            print(
                _dim(
                    "  PDFs may still be in-flight — try again in a moment or use --follow"
                )
            )
            print(
                _dim(
                    f"  Raw logs: aws logs tail {log_group} --follow --region {REGION}"
                )
            )
            print(_dim(_SEP))
            print()
            return set()

        ok_count = sum(1 for i in invocations if i["status"] == "ok")
        err_count = sum(1 for i in invocations if i["status"] == "error")

        for inv in invocations:
            label = (
                inv["file_key"].split("/")[-1]
                if inv["file_key"]
                else f"req:{inv['req'][:8]}"
            )
            dur = f"  {inv['duration']:.0f}s" if inv["duration"] else ""

            if inv["status"] == "ok":
                secs = (
                    f"  sections={inv['sections']}"
                    if inv["sections"] is not None
                    else ""
                )
                pgs = f"  pages={inv['pages']}" if inv["pages"] is not None else ""
                print(
                    f"  {_green('OK')}  {_dim(inv['ts'])}  {label}{_dim(secs + pgs + dur)}"
                )
            else:
                print(f"  {_red('ERR')} {_dim(inv['ts'])}  {label}{_dim(dur)}")
                for snippet in inv["errors"][:1]:
                    if verbose:
                        print(f"       {_red(snippet)}")
                    else:
                        truncated = snippet[:80] + ("…" if len(snippet) > 80 else "")
                        print(f"       {_yellow(truncated)}")
                if verbose and len(inv["errors"]) > 1:
                    for snippet in inv["errors"][1:3]:
                        print(f"       {_red(snippet)}")
                if verbose and inv.get("req"):
                    print(_dim(f"       req: {inv['req']}"))

        print()
        if err_count == 0 and ok_count > 0:
            print(_green(f"  All {ok_count} invocation(s) completed successfully."))
        else:
            status_str = (
                _green(f"{ok_count} ok")
                + "  "
                + (_red(f"{err_count} failed") if err_count else _dim("0 failed"))
            )
            print(f"  {ok_count + err_count} invocation(s) total  —  {status_str}")
            if err_count and not verbose:
                print(_dim("  Use --verbose for full error details and raw logs"))

        if verbose:
            print()
            print(
                _dim(
                    f"  Raw logs: aws logs tail {log_group} --follow --region {REGION}"
                )
            )

        print(_dim(_SEP))
        print()
        return {
            i["file_key"] for i in invocations if i["file_key"] and i["status"] == "ok"
        }

    cw_query = (
        "fields @timestamp, @requestId, @message "
        "| filter @message like /ERROR/ or @message like /sections_indexed/ "
        "    or @message like /No sections extracted/ or @message like /REPORT RequestId/ "
        "| sort @timestamp asc "
        "| limit 500"
    )

    if not follow:
        results = _run_query(cw_query)
        if results is None:
            print(
                _red(
                    "  Could not query CloudWatch Logs Insights. Check AWS credentials / log group name."
                )
            )
            return
        invocations = _parse(results)
        _print_summary(invocations)
    else:
        print(
            _bold("==>")
            + f" Watching {lambda_name} — polling every 15 s  (Ctrl+C to stop)"
        )
        seen: set[str] = set()
        try:
            while True:
                results = _run_query(cw_query)
                if results is None:
                    print(_yellow("  Query failed — retrying..."))
                else:
                    invocations = _parse(results)
                    completed = _print_summary(invocations)
                    new = completed - seen
                    if new:
                        seen.update(new)
                _time.sleep(15)
        except KeyboardInterrupt:
            print(_dim("\n==> Stopped."))


# --------------------------------------------------------------------------- #
# Namespaces
# --------------------------------------------------------------------------- #
env = Collection("env")
env.add_task(up)
env.add_task(plan)
env.add_task(down)
env.add_task(list)
env.add_task(bootstrap)
env.add_task(endpoints)

frontend_ns = Collection("frontend")
frontend_ns.add_task(deploy)

ollama = Collection("ollama")
ollama.add_task(health_check)
ollama.add_task(restart_ollama)
ollama.add_task(pull_model)
ollama.add_task(logs)

lambda_ns = Collection("lambda")
lambda_ns.add_task(pull)
lambda_ns.add_task(build_layer, name="build-layer")
lambda_ns.add_task(invoke)
lambda_ns.add_task(invoke_all, name="invoke-all")
lambda_ns.add_task(pdf_async_status, name="pdf-async-status")
lambda_ns.add_task(set_env, name="set-env")
lambda_ns.add_task(lambda_logs, name="logs")
lambda_ns.add_task(lambda_status, name="status")

bedrock_ns = Collection("bedrock")
bedrock_ns.add_task(model_access, name="model-access")

opensearch_ns = Collection("opensearch")
opensearch_ns.add_task(opensearch_status, name="status")

ns = Collection()
ns.add_collection(env)
ns.add_collection(frontend_ns)
ns.add_collection(ollama)
ns.add_collection(lambda_ns)
ns.add_collection(bedrock_ns)
ns.add_collection(opensearch_ns)
