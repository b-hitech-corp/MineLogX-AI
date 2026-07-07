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

Full DEV flow (first time):
  uv run fab env.bootstrap                         # create S3 bucket (for nested template uploads)
  uv run fab lambda.build-layer csv
  uv run fab lambda.build-layer pdf
  uv run fab env.up dev                            # cloudformation deploy with layers
  uv run fab frontend.deploy dev                   # build React + push to Amplify

Note: use the long flag `--engine` — Fabric reserves the short `-e` for --echo.

Tip: prefer `uv run fab` — it finds terraform via the shell PATH regardless of
venv activation. If terraform still isn't found, set TERRAFORM_BIN (see below).
"""

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


def _cfn(c, env, execute, build_pdf_layer=False, build_csv_layer=False):
    """Package + deploy the single parent stack (nested children) as minelogx-<env>.

    `package` uploads the child templates to S3 and rewrites TemplateURLs.
    execute=False creates a change set without applying (plan).
    """
    apn = _apn(env)
    fallback = "true" if env == "prod" else "false"
    pdf_layer = "true" if build_pdf_layer else "false"
    csv_layer = "true" if build_csv_layer else "false"
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
            f"BuildPdfLayer={pdf_layer} BuildCsvLayer={csv_layer} "
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
        "build_pdf_layer": "Attach the PDF deps layer (must run `fab lambda.build-layer pdf` first).",
        "build_csv_layer": "Attach the CSV deps layer (must run `fab lambda.build-layer csv` first).",
    },
)
def up(c, env, engine="cloudformation", build_pdf_layer=False, build_csv_layer=False):
    """Create/update an environment (fixed or ephemeral)."""
    engine = _norm_engine(engine)
    _ensure_aws(c)
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
            f'-var="build_pdf_layer={"true" if build_pdf_layer else "false"}"',
            f'-var="build_csv_layer={"true" if build_csv_layer else "false"}"',
        )
        return

    _cfn(
        c,
        env,
        execute=True,
        build_pdf_layer=build_pdf_layer,
        build_csv_layer=build_csv_layer,
    )


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

    # CloudFormation: one parent stack — deleting it removes all nested children.
    c.run(
        f"aws cloudformation delete-stack --stack-name {NAME_PREFIX}-{env} --region {REGION}",
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
LAMBDA_LAYERS_DIR = TARGET_ROOT / "backend" / ".layers"

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
    # manylinux2014 (glibc 2.17) — matches Amazon Linux 2, which backs the
    # Lambda python3.11 runtime (glibc 2.26). Newer manylinux_2_28 wheels
    # (glibc >=2.28, e.g. PyMuPDF >=1.26) would import-error at runtime — pin
    # deps to versions that still ship a manylinux2014 wheel.
    c.run(
        f'"{sys.executable}" -m pip install '
        "--platform manylinux2014_x86_64 --python-version 3.11 "
        "--implementation cp --abi cp311 --only-binary=:all: "
        f'--target "{python_dir}" -r "{reqs}"'
    )

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


@task(
    help={
        "env": "Environment to deploy the frontend to (dev|qa|prod|dev-<user>).",
        "skip_build": "Skip `pnpm build` (use existing dist/ — useful for re-deploys).",
    },
)
def deploy(c, env, skip_build=False):
    """Build the React/Vite frontend and push it to Amplify (manual deployment).

    Steps:
      1. pnpm install + pnpm build  (skippable with --skip-build)
      2. Zip shared/frontend/dist/
      3. Create an Amplify manual deployment job (returns a presigned S3 upload URL)
      4. PUT the zip to the presigned URL
      5. Start the deployment job and poll until SUCCEED/FAILED
    """
    _ensure_aws(c)
    print(f"==> frontend.deploy: env={env} region={REGION}")

    dist_dir = FRONTEND_DIR / "dist"

    if not skip_build:
        pnpm = shutil.which("pnpm") or "pnpm"
        print(f"==> Building frontend in {FRONTEND_DIR}")
        with c.cd(str(FRONTEND_DIR)):
            c.run(f'"{pnpm}" install --frozen-lockfile')
            c.run(f'"{pnpm}" build')

    if not dist_dir.exists():
        raise SystemExit(
            f"{dist_dir} not found. Run `pnpm build` in shared/frontend/ first, "
            "or omit --skip-build."
        )

    # Zip the dist directory into a temp file.
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        zip_path = tmp.name
    try:
        print(f"==> Zipping {dist_dir} -> {zip_path}")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in dist_dir.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(dist_dir))

        app_id = _amplify_app_id(c, env)
        branch = _amplify_branch(c, app_id)
        print(f"==> Amplify app={app_id} branch={branch}")

        # Create a manual deployment job — Amplify returns a presigned upload URL.
        create_result = c.run(
            f"aws amplify create-deployment --app-id {app_id} "
            f"--branch-name {branch} --region {REGION} --output json",
            hide=True,
        )
        import json as _json

        deployment = _json.loads(create_result.stdout)
        job_id = deployment["jobId"]
        upload_url = deployment["zipUploadUrl"]
        print(f"==> Deployment job created: jobId={job_id}")

        # Upload the zip to the presigned S3 URL.
        print("==> Uploading dist.zip to Amplify presigned URL...")
        urllib.request.urlopen(  # nosec B310 - URL comes from AWS API response
            urllib.request.Request(
                upload_url,
                data=open(zip_path, "rb").read(),  # noqa: WPS515
                method="PUT",
                headers={"Content-Type": "application/zip"},
            )
        )

        # Start the deployment.
        c.run(
            f"aws amplify start-deployment --app-id {app_id} "
            f"--branch-name {branch} --job-id {job_id} --region {REGION}",
            hide=True,
        )
        print("==> Deployment started. Polling for completion...")

        # Poll until done (max ~5 min).
        import time as _time

        for _ in range(60):
            status_result = c.run(
                f"aws amplify get-job --app-id {app_id} "
                f"--branch-name {branch} --job-id {job_id} --region {REGION} "
                '--query "job.summary.status" --output text',
                hide=True,
            )
            status = status_result.stdout.strip()
            if status in ("SUCCEED", "FAILED", "CANCELLED"):
                break
            print(f"    status={status} — waiting 5s...")
            _time.sleep(5)

        if status == "SUCCEED":
            print(
                f"\n==> Frontend deployed! Status={status}\n"
                f"    URL: https://{branch}.{app_id}.amplifyapp.com"
            )
        else:
            raise SystemExit(
                f"Amplify deployment {status}. Check the console for logs."
            )
    finally:
        os.unlink(zip_path)


# --------------------------------------------------------------------------- #
# Namespaces
# --------------------------------------------------------------------------- #
env = Collection("env")
env.add_task(up)
env.add_task(plan)
env.add_task(down)
env.add_task(list)
env.add_task(bootstrap)

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

ns = Collection()
ns.add_collection(env)
ns.add_collection(frontend_ns)
ns.add_collection(ollama)
ns.add_collection(lambda_ns)
