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
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# Windows cp1252 terminals crash on Unicode chars (✓, →, etc.) — force UTF-8.
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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


def _mkdocs_bin():
    """Resolve mkdocs from the project venv (Windows Scripts/ vs POSIX bin/), falling back to PATH."""
    venv_bin = (
        REPO_ROOT
        / ".venv"
        / ("Scripts/mkdocs.exe" if os.name == "nt" else "bin/mkdocs")
    )
    return venv_bin if venv_bin.exists() else (shutil.which("mkdocs") or "mkdocs")


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
    """Sync data from demo seed buckets into the minelogx-{env}-* buckets (excluding operational logs)."""
    for suffix, src in DEMO_SEED_BUCKETS.items():
        dst = f"{NAME_PREFIX}-{env}-{suffix}"
        print(f"==> seeding s3://{src}/ -> s3://{dst}/")
        c.run(
            f'aws s3 sync s3://{src}/ s3://{dst}/ --region {REGION} --exclude "logs/*"'
        )


def _s3_pipeline_data_present(c, env):
    """True if the telemetry or legislation bucket already has files to ingest."""
    for suffix, ext in (("telemetry-data", ".csv"), ("legislation-documents", ".pdf")):
        bucket = f"{NAME_PREFIX}-{env}-{suffix}"
        r = c.run(
            f"aws s3 ls s3://{bucket}/ --recursive --query 'Contents[].Key' --output text",
            hide=True,
            warn=True,
        )
        if r.ok and any(k.lower().endswith(ext) for k in r.stdout.split()):
            return True
    return False


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


def _cfn(
    c, env, execute, build_pdf_layer=False, build_csv_layer=False, build_api_layer=False
):
    """Package + deploy the single parent stack (nested children) as minelogx-<env>.

    `package` uploads the child templates to S3 and rewrites TemplateURLs.
    execute=False creates a change set without applying (plan).
    """
    apn = _apn(env)
    fallback = "true" if env == "prod" else "false"
    pdf_layer = "true" if build_pdf_layer else "false"
    csv_layer = "true" if build_csv_layer else "false"
    api_layer = "true" if build_api_layer else "false"
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
            f"BuildPdfLayer={pdf_layer} BuildCsvLayer={csv_layer} BuildApiLayer={api_layer}"
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
        "skip_pipelines": "Skip running ALL pipelines: csv, pdf, and analysis (infra + frontend + docs only).",
    },
)
def up(
    c,
    env,
    engine="cloudformation",
    seed=False,
    no_rollback=False,
    skip_frontend=False,
    skip_pipelines=False,
):
    """Create/update an environment. Builds Lambda layers, deploys infra, and deploys the frontend."""
    engine = _norm_engine(engine)
    _ensure_aws(c)
    print(f"==> up: env={env} engine={engine} region={REGION}")

    # Auto-recover ROLLBACK_COMPLETE: delete stale stack before re-creating.
    _stack_name = f"{NAME_PREFIX}-{env}"
    _sr = c.run(
        f"aws cloudformation describe-stacks --stack-name {_stack_name} "
        f"--region {REGION} --query 'Stacks[0].StackStatus' --output text",
        hide=True,
        warn=True,
    )
    if _sr.ok and _sr.stdout.strip() == "ROLLBACK_COMPLETE":
        print(
            _yellow(
                f"  [!] Stack '{_stack_name}' en ROLLBACK_COMPLETE — eliminando para re-crear..."
            )
        )
        c.run(
            f"aws cloudformation delete-stack --stack-name {_stack_name} --region {REGION}",
            hide=True,
        )
        c.run(
            f"aws cloudformation wait stack-delete-complete --stack-name {_stack_name} --region {REGION}",
            hide=True,
        )
        print(_green(f"  Stack '{_stack_name}' eliminado. Continuando con deploy..."))

    log_path = _up_log_path(env)

    try:
        # --- 1. Build Lambda layers (always, so the zip is ready for CFN package) ---
        for fn in ("api", "csv", "pdf"):
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
                '-var="build_api_layer=true"',
            )
        else:
            _cfn(
                c,
                env,
                execute=True,
                build_pdf_layer=True,
                build_csv_layer=True,
                build_api_layer=True,
            )

        # --- 3. Seed S3 (dev only): explicit --seed, or auto-seed when empty ---
        if seed and env not in SEED_ALLOWED_ENVS:
            print(
                f"[warn] --seed ignored for '{env}': only allowed in {SEED_ALLOWED_ENVS}."
            )
        elif seed:
            _s3_seed(c, env)
        elif env in SEED_ALLOWED_ENVS and not _s3_pipeline_data_present(c, env):
            print(
                "[info] --seed not passed but buckets are empty — auto-seeding demo data."
            )
            _s3_seed(c, env)

        # --- 4. Endpoints summary (includes ApiUrl for frontend build) ---
        outputs, _stack_status = _endpoints_data(c, env)
        if outputs:
            _show_and_save_endpoints(env, outputs)
        else:
            print(
                "[warn] Sin outputs de CloudFormation — omitiendo resumen de endpoints."
            )

        _frontend_url = ""
        _docs_url = ""

        # --- 5. Frontend build + deploy (inyecta VITE_API_BASE_URL dinámicamente) ---
        frontend_app_id = next(
            (
                o["OutputValue"]
                for o in (outputs or [])
                if o["OutputKey"] == "AmplifyAppId"
            ),
            "",
        )
        if not skip_frontend:
            api_url = next(
                (
                    o["OutputValue"]
                    for o in (outputs or [])
                    if o["OutputKey"] == "ApiUrl"
                ),
                "",
            )
            analyze_url = next(
                (
                    o["OutputValue"]
                    for o in (outputs or [])
                    if o["OutputKey"] == "AnalyzeUrl"
                ),
                "",
            )
            _frontend_build_and_deploy(c, env, api_url=api_url, analyze_url=analyze_url)
            _frontend_url = _amplify_frontend_url(c, env)
        elif frontend_app_id:
            _frontend_url = f"https://demo.{frontend_app_id}.amplifyapp.com"
        if _frontend_url:
            print(f"\n  {_bold('Frontend URL:')} {_cyan(_frontend_url)}")

        # --- 6. Docs deploy (mkdocs → Amplify docs app, app_id from CFN outputs) ---
        docs_app_id = next(
            (
                o["OutputValue"]
                for o in (outputs or [])
                if o["OutputKey"] == "AmplifyDocsAppId"
            ),
            "",
        )
        print()
        if not docs_app_id:
            print(
                "==> [docs] Skipped — AmplifyDocsAppId not in stack outputs (run env.up to provision the docs Amplify app first)."
            )
        else:
            _docs_url = f"https://demo.{docs_app_id}.amplifyapp.com"
            hash_file = LOGS_DIR / f"docs-deploy-{env}.hash"
            current_hash = _docs_content_hash()
            last_hash = (
                hash_file.read_text(encoding="utf-8").strip()
                if hash_file.exists()
                else ""
            )
            if current_hash == last_hash:
                print(
                    f"==> [docs] Skipped — no changes detected since last deploy (env={env}). Use `fab docs.deploy {env} --force` to override."
                )
            else:
                print("==> [docs] Building documentation...")
                mkdocs_bin = _mkdocs_bin()
                result = c.run(f'"{mkdocs_bin}" build --strict', hide=True, warn=True)
                if not result.ok:
                    print(
                        f"[warn] mkdocs build failed — skipping docs deploy:\n{result.stderr}"
                    )
                else:
                    dist_dir = REPO_ROOT / "site"
                    print(f"==> [docs] Deploying to Amplify app={docs_app_id}...")
                    _amplify_upload_and_poll(
                        c, env, dist_dir, lambda m: print(m), app_id=docs_app_id
                    )
                    hash_file.write_text(current_hash, encoding="utf-8")
            if _docs_url:
                print(f"\n  {_bold('Docs URL:')} {_cyan(_docs_url)}")

        # --- 7. Run ingestion pipelines (csv + pdf) so OpenSearch isn't left empty ---
        _csv_status = ""
        _pdf_status = ""
        if skip_pipelines:
            print("\n==> [pipelines] Skipped (--skip-pipelines).")
        else:
            print("\n==> [pipelines] Running csv ingestion...")
            try:
                ok, fail = invoke_all(c, "csv", env, parallel=True)
                _csv_status = "OK" if fail == 0 else f"FAILED ({fail}/{ok + fail})"
            except (SystemExit, Exception) as exc:
                _csv_status = f"FAILED: {exc}"
                print(f"[warn] csv pipeline failed: {exc}")

            print("\n==> [pipelines] Running pdf ingestion...")
            try:
                ok, fail = invoke_all(c, "pdf", env)
                _pdf_status = "OK" if fail == 0 else f"FAILED ({fail}/{ok + fail})"
            except (SystemExit, Exception) as exc:
                _pdf_status = f"FAILED: {exc}"
                print(f"[warn] pdf pipeline failed: {exc}")

        # --- 8. Analysis pipeline (same skip flag as csv/pdf) ---
        _analysis_status = ""
        if not skip_pipelines:
            print("\n==> [pipelines] Running analysis ingest...")
            try:
                ok, fail = analysis_ingest(c, env, force=True)
                _analysis_status = "OK" if fail == 0 else f"FAILED ({fail}/{ok + fail})"
            except (SystemExit, Exception) as exc:
                _analysis_status = f"FAILED: {exc}"
                print(f"[warn] analysis ingest failed: {exc}")

        # Re-save endpoints-<env>.md with FrontendUrl + DocsUrl + pipeline status rows.
        if outputs:
            _show_and_save_endpoints(
                env,
                outputs,
                frontend_url=_frontend_url,
                docs_url=_docs_url,
                csv_status=_csv_status,
                pdf_status=_pdf_status,
                analysis_status=_analysis_status,
            )

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
        "fn": "Function whose deps to build: api | csv | pdf (each has its own requirements-<fn>.txt).",
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

    # ponytail: hash stored in .lambda-layers/.hash-{fn}, delete to force rebuild
    hash_file = LAMBDA_LAYERS_DIR / f".hash-{fn}"
    current_hash = hashlib.sha256(reqs.read_bytes()).hexdigest()
    if (
        hash_file.exists()
        and hash_file.read_text().strip() == current_hash
        and build_dir.exists()
    ):
        print(
            f"==> [build-layer] Layer '{fn}' up-to-date (requirements unchanged) — skipping."
        )
        return

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

    # ponytail: hardcoded allowlist from grepping every boto3.client(...) call
    # across backend/ — add the service here if a new one is introduced, or
    # botocore raises UnknownServiceError at runtime.
    _KEEP_BOTOCORE_SERVICES = {"s3", "s3vectors", "bedrock-runtime", "sts", "textract"}
    botocore_data_dir = python_dir / "botocore" / "data"
    if botocore_data_dir.exists():
        for svc_dir in botocore_data_dir.iterdir():
            if svc_dir.is_dir() and svc_dir.name not in _KEEP_BOTOCORE_SERVICES:
                shutil.rmtree(svc_dir)

    size = _dir_size(build_dir)
    print(f"==> Layer '{fn}' built: {size / 1024 / 1024:.1f} MB decompressed")
    if size > LAYER_SIZE_WARN_BYTES:
        print(
            "    WARNING: over the safety margin for the 250MB Lambda code+layers "
            "limit — trim requirements or split further before deploying."
        )
    hash_file.write_text(current_hash)
    build_flag = {
        "api": "--build-api-layer",
        "csv": "--build-csv-layer",
        "pdf": "--build-pdf-layer",
    }.get(fn, f"--build-{fn}-layer")
    next_msg = f"Next: fab env.plan <env> --engine tf {build_flag} (or --engine cf)."
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
        f"--query 'apps[?name==`{app_name}`].appId' --output text",
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


def _amplify_upload_and_poll(c, env, dist_dir, log, app_id=None):
    """Zip dist/, upload to Amplify via presigned URL, poll until SUCCEED/FAILED.

    Returns the deployed URL on success; raises SystemExit on failure.
    Pass app_id to skip the automatic discovery (used by docs.deploy).
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

        if app_id is None:
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
        elapsed = 0
        max_poll = 300  # 5 min hard cap
        bar_width = 28
        GREEN = "\033[32m"
        RESET = "\033[0m"
        TICK = 5

        for _ in range(max_poll // TICK):
            res = c.run(
                f"aws amplify get-job --app-id {app_id} "
                f"--branch-name {branch} --job-id {job_id} --region {REGION} "
                '--query "job.summary.status" --output text',
                hide=True,
            )
            status = res.stdout.strip()
            if status in ("SUCCEED", "FAILED", "CANCELLED"):
                break
            elapsed += TICK
            pct = min(int(elapsed / max_poll * 100), 99)
            filled = int(bar_width * pct / 100)
            bar = "█" * filled + "░" * (bar_width - filled)
            sys.stdout.write(
                f"\r    {GREEN}[{bar}]{RESET} {pct:>3}%  {elapsed}s elapsed  "
            )
            sys.stdout.flush()
            _time.sleep(TICK)

        # Clear the progress line
        sys.stdout.write(f"\r{' ' * (bar_width + 30)}\r")
        sys.stdout.flush()

        url = f"https://{branch}.{app_id}.amplifyapp.com"
        if status == "SUCCEED":
            elapsed += TICK
            bar = "█" * bar_width
            sys.stdout.write(f"    {GREEN}[{bar}]{RESET} 100%  {elapsed}s  ✓\n")
            sys.stdout.flush()
            log(f"    URL: {url}")
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


def _frontend_build_and_deploy(c, env, api_url="", analyze_url=""):
    """Build the React/Vite app with a live API URL, then upload to Amplify.

    Injects VITE_API_BASE_URL dynamically so it always reflects the current
    stack — safe even after env.down + env.up.
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
    _log(
        f"==> [frontend] VITE_ANALYZE_URL={analyze_url or '(empty — falls back to VITE_API_BASE_URL)'}"
    )

    build_env = {
        **os.environ,
        "VITE_API_BASE_URL": api_url,
        # Lambda Function URLs always end in '/'; strip it so apiFetch's
        # `${baseUrl}${endpoint}` concatenation matches VITE_API_BASE_URL's shape.
        "VITE_ANALYZE_URL": analyze_url.rstrip("/"),
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
    outputs = None
    if not resolved_url:
        outputs, _ = _endpoints_data(c, env)
        resolved_url = next(
            (o["OutputValue"] for o in outputs if o["OutputKey"] == "ApiUrl"), ""
        )

    if outputs is None:
        outputs, _ = _endpoints_data(c, env)
    analyze_url = next(
        (o["OutputValue"] for o in outputs if o["OutputKey"] == "AnalyzeUrl"), ""
    )

    if skip_build:
        dist_dir = FRONTEND_DIR / "dist"
        if not dist_dir.exists():
            raise SystemExit(f"{dist_dir} not found. Run without --skip-build first.")
        _amplify_upload_and_poll(c, env, dist_dir, print)
    else:
        _frontend_build_and_deploy(
            c, env, api_url=resolved_url, analyze_url=analyze_url
        )


@task(
    positional=["env"],
    help={
        "env": "Environment name (dev|qa|prod|dev-<user>).",
        "timeout": "Request timeout in seconds (default: 15).",
    },
)
def validate(c, env, timeout=15):
    """Validate that all frontend endpoints (API GW GET/POST) are reachable.

    Checks each API Gateway route for HTTP 200, JSON content-type, and CORS headers.
    Also validates LLM routes (/chat, /analyze) are reachable through the same gateway.
    """
    import time as _time
    import urllib.error as _ue
    import urllib.request as _ur

    _ensure_aws(c)
    ts = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\n  Frontend Validation — {NAME_PREFIX}-{env}  ({ts})\n")

    outputs, stack_status = _endpoints_data(c, env)
    if not outputs:
        raise SystemExit(
            f"Stack {NAME_PREFIX}-{env} not found or has no outputs (status={stack_status})"
        )

    api_url = next(
        (o["OutputValue"] for o in outputs if o["OutputKey"] == "ApiUrl"), ""
    )
    analyze_url = next(
        (o["OutputValue"] for o in outputs if o["OutputKey"] == "AnalyzeUrl"), ""
    )
    if not api_url:
        raise SystemExit("ApiUrl not found in stack outputs")

    amplify_domain = next(
        (o["OutputValue"] for o in outputs if o["OutputKey"] == "AmplifyDomain"),
        "",
    )
    if amplify_domain and not amplify_domain.startswith("http"):
        amplify_domain = f"https://{amplify_domain}"

    _API_ROUTES = [
        "/fleet/assets",
        "/kpis",
        "/fuel/records",
        "/fuel/trend",
        "/maintenance/items",
        "/maintenance/work-orders",
        "/telemetry/gps",
        "/telemetry/zones",
    ]

    passed = 0
    failed = 0

    def _req_get(url, method="GET", data=None, headers=None):
        req = _ur.Request(url, method=method, data=data)
        if amplify_domain:
            req.add_header("Origin", amplify_domain.rstrip("/"))
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        return req

    # --- API Gateway routes ---
    print("  API Gateway (VITE_API_BASE_URL)")
    print("  " + "─" * 65)
    for route in _API_ROUTES:
        url = api_url.rstrip("/") + route
        t0 = _time.monotonic()
        try:
            with _ur.urlopen(_req_get(url), timeout=timeout) as resp:  # nosec B310
                elapsed = int((_time.monotonic() - t0) * 1000)
                ct = resp.headers.get("Content-Type", "")
                cors = resp.headers.get("Access-Control-Allow-Origin", "")
                ok = resp.status == 200 and "application/json" in ct and bool(cors)
                passed += ok
                failed += not ok
                json_tag = "JSON" if "application/json" in ct else "NOT-JSON"
                cors_tag = "CORS ✓" if cors else "CORS ✗"
                print(
                    f"  {route:<32}  {resp.status}  {json_tag}  {cors_tag}   {elapsed} ms"
                )
        except Exception as exc:
            elapsed = int((_time.monotonic() - t0) * 1000)
            failed += 1
            print(f"  {route:<32}  ERR  {exc}   {elapsed} ms")

    # CORS preflight
    preflight_url = api_url.rstrip("/") + "/fleet/assets"
    req = _ur.Request(preflight_url, method="OPTIONS")
    req.add_header("Origin", amplify_domain or "https://example.com")
    req.add_header("Access-Control-Request-Method", "GET")
    t0 = _time.monotonic()
    try:
        with _ur.urlopen(req, timeout=timeout) as resp:  # nosec B310
            elapsed = int((_time.monotonic() - t0) * 1000)
            ok = resp.status in (200, 204)
            passed += ok
            failed += not ok
            print(
                f"  OPTIONS /fleet/assets                   {resp.status}  CORS preflight {'✓' if ok else '✗'}   {elapsed} ms"
            )
    except _ue.HTTPError as exc:
        elapsed = int((_time.monotonic() - t0) * 1000)
        ok = exc.code in (200, 204)
        passed += ok
        failed += not ok
        print(
            f"  OPTIONS /fleet/assets                   {exc.code}  CORS preflight {'✓' if ok else '✗'}   {elapsed} ms"
        )
    except Exception as exc:
        elapsed = int((_time.monotonic() - t0) * 1000)
        failed += 1
        print(f"  OPTIONS /fleet/assets                   ERR  {exc}   {elapsed} ms")

    # --- LLM routes (chat/analyze) ---
    # /chat stays fast on API Gateway (short default timeout). /analyze runs
    # FolderPipeline's agentic loop and can genuinely take minutes, and now
    # goes through its own Function URL (no 29-30s API Gateway proxy cap) —
    # give it a much longer client-side read timeout.
    ANALYZE_TIMEOUT = 600
    print()
    print("  LLM routes (chat/analyze)")
    print("  " + "─" * 65)
    for route, payload, base, req_timeout in (
        ("/chat", b'{"query":"ping","model":"nova","client":"C1"}', api_url, timeout),
        ("/analyze", b'{"company":"c1"}', analyze_url, ANALYZE_TIMEOUT),
    ):
        if not base:
            print(f"  POST {route:<27}  SKIP  (AnalyzeUrl not found in stack outputs)")
            continue
        url = base.rstrip("/") + route
        req = _req_get(
            url,
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        t0 = _time.monotonic()
        try:
            with _ur.urlopen(req, timeout=req_timeout) as resp:  # nosec B310
                elapsed = int((_time.monotonic() - t0) * 1000)
                passed += 1
                print(f"  POST {route:<27}  {resp.status} ✓   {elapsed} ms")
        except _ue.HTTPError as exc:
            # ponytail: 4xx/5xx still means the route is wired up — only network errors fail
            elapsed = int((_time.monotonic() - t0) * 1000)
            passed += 1
            print(f"  POST {route:<27}  {exc.code} (reachable)   {elapsed} ms")
        except Exception as exc:
            elapsed = int((_time.monotonic() - t0) * 1000)
            failed += 1
            print(f"  POST {route:<27}  ERR  {exc}   {elapsed} ms")

    total = passed + failed
    print()
    print(f"  Summary: {passed}/{total} passed  [FAIL: {failed}]")
    if failed:
        raise SystemExit(f"\n  {failed} check(s) failed.")


# --------------------------------------------------------------------------- #
# env.endpoints helpers
# --------------------------------------------------------------------------- #


def _endpoints_data(c, env):
    """Return (outputs, stack_status) for env. outputs=[] when stack is absent or has no Outputs."""
    import json as _json

    stack_name = f"{NAME_PREFIX}-{env}"
    result = c.run(
        f"aws cloudformation describe-stacks --stack-name {stack_name} "
        f'--region {REGION} --query "Stacks[0]" --output json',
        hide=True,
        warn=True,
    )
    if not result.ok:
        stderr = result.stderr.strip()
        if "does not exist" in stderr or "ValidationError" in stderr:
            return [], "NOT_FOUND"
        return [], f"ERROR: {stderr or 'no details — check AWS credentials'}"
    raw = result.stdout.strip()
    if not raw or raw == "null":
        return [], "NOT_FOUND"
    stack = _json.loads(raw)
    return stack.get("Outputs") or [], stack.get("StackStatus", "UNKNOWN")


def _amplify_branch_url(c, app_name):
    """Return the live branch URL for an Amplify app by name, or '' if not found."""
    import json as _json

    r = c.run(
        f"aws amplify list-apps --region {REGION} --output json",
        hide=True,
        warn=True,
    )
    if not r.ok:
        return ""
    apps = _json.loads(r.stdout).get("apps", [])
    app = next((a for a in apps if a["name"] == app_name), None)
    if not app:
        return ""
    app_id = app["appId"]
    br = c.run(
        f"aws amplify list-branches --app-id {app_id} --region {REGION} --output json",
        hide=True,
        warn=True,
    )
    branches = _json.loads(br.stdout).get("branches", []) if br.ok else []
    branch = branches[0]["branchName"] if branches else "main"
    return f"https://{branch}.{app_id}.amplifyapp.com"


def _amplify_frontend_url(c, env):
    return _amplify_branch_url(c, f"{NAME_PREFIX}-{env}-frontend")


def _amplify_docs_url(c, env):
    return _amplify_branch_url(c, f"{NAME_PREFIX}-{env}-docs")


def _show_and_save_endpoints(
    env,
    outputs,
    frontend_url="",
    docs_url="",
    csv_status="",
    pdf_status="",
    analysis_status="",
):
    """Pretty-print CFN outputs and write endpoints-<env>.md to the repo root."""
    import datetime as _dt
    from tabulate import tabulate as _tabulate

    rows = []
    for o in outputs:
        key = o["OutputKey"]
        val = o["OutputValue"]
        display_val = _cyan(val) if val.startswith("http") else val
        rows.append([key, display_val])
    if frontend_url:
        rows.append(["FrontendUrl", _cyan(frontend_url)])
    if docs_url:
        rows.append(["DocsUrl", _cyan(docs_url)])
    if csv_status:
        rows.append(
            ["CsvPipeline", csv_status if csv_status == "OK" else _red(csv_status)]
        )
    if pdf_status:
        rows.append(
            ["PdfPipeline", pdf_status if pdf_status == "OK" else _red(pdf_status)]
        )
    if analysis_status:
        rows.append(
            [
                "AnalysisPipeline",
                analysis_status if analysis_status == "OK" else _red(analysis_status),
            ]
        )

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
    if frontend_url:
        lines.append(
            f"| `FrontendUrl` | {frontend_url} | Frontend Amplify (branch URL) |"
        )
    if docs_url:
        lines.append(f"| `DocsUrl` | {docs_url} | Documentation site (branch URL) |")
    if csv_status:
        lines.append(
            f"| `CsvPipeline` | {csv_status} | CSV telemetry ingestion result |"
        )
    if pdf_status:
        lines.append(
            f"| `PdfPipeline` | {pdf_status} | PDF legislation ingestion result |"
        )
    if analysis_status:
        lines.append(
            f"| `AnalysisPipeline` | {analysis_status} | Data analysis results ingestion |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Guardado → {md_path.name}")


@task(help={"env": "Environment name (dev|qa|prod|dev-<user>)."})
def health(c, env):
    """Comprehensive health check: Lambda states, AOSS doc counts, Step Functions history, Bedrock access.

    Combines the output of lambda.status, opensearch.status, recent Step Functions
    executions, and bedrock.model-access into a single summary report. Useful to
    quickly verify the environment is fully operational after a deploy or re-ingest.

    Examples:
      uv run fab env.health dev
      uv run fab env.health qa
    """
    _ensure_aws(c)
    from tabulate import tabulate as _tabulate

    ts = datetime.datetime.now().isoformat(timespec="seconds")
    lines = [
        "",
        _SEP,
        f"  env.health — {NAME_PREFIX}-{env}  ({ts})",
        _SEP,
    ]

    # --- 1. Lambda states ---
    lines += ["", _bold("  [1/4] Lambda Functions"), ""]
    lambda_rows = []
    for name in LAMBDA_NAMES:
        fn_name = _lambda_fn(env, name)
        res = c.run(
            f"aws lambda get-function-configuration --function-name {fn_name} "
            f"--region {REGION} --output json",
            hide=True,
            warn=True,
        )
        if not res.ok:
            lambda_rows.append([fn_name, _red("NOT FOUND"), "—", "—"])
            continue
        cfg = json.loads(res.stdout)
        state = cfg.get("State", "?")
        state_col = _green(state) if state == "Active" else _yellow(state)
        last = cfg.get("LastModified", "?")[:19].replace("T", " ")
        timeout = f"{cfg.get('Timeout', '?')}s"
        lambda_rows.append([fn_name, state_col, timeout, last])
    table = _tabulate(
        lambda_rows,
        headers=["Function", "State", "Timeout", "Last Deploy"],
        tablefmt="simple",
        disable_numparse=True,
    )
    for line in table.splitlines():
        lines.append(f"  {line}")

    # --- 2. AOSS collection + doc counts ---
    lines += ["", _bold("  [2/4] OpenSearch Serverless"), ""]
    outputs, _ = _endpoints_data(c, env)
    endpoint = next(
        (o["OutputValue"] for o in outputs if o["OutputKey"] == "OpenSearchEndpoint"),
        None,
    )
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
    if collections:
        col = collections[0]
        col_status = col.get("status", "?")
        col_status_col = (
            _green(col_status) if col_status == "ACTIVE" else _yellow(col_status)
        )
        lines.append(f"\n  Collection   : {col.get('name')}  status={col_status_col}")
    else:
        lines.append(f"\n  {_red('Collection not found:')} {col_name}")
    if endpoint:
        idx_rows = []
        for idx in OPENSEARCH_INDICES:
            try:
                data = _aoss_get(endpoint, f"/{idx}/_count")
                count = (
                    f"{data['count']:,}"
                    if isinstance(data.get("count"), int)
                    else str(data.get("count", "?"))
                )
                idx_rows.append([idx, count, _green("OK")])
            except Exception as exc:  # noqa: BLE001
                idx_rows.append([idx, "—", _red(f"ERROR: {exc}")])
        table = _tabulate(
            idx_rows,
            headers=["Index", "Docs", "Status"],
            tablefmt="simple",
            disable_numparse=True,
        )
        for line in table.splitlines():
            lines.append(f"  {line}")
    else:
        lines.append("  (endpoint unavailable — run `fab env.up dev` first)")

    # --- 3. Step Functions — last 5 CSV pipeline executions ---
    lines += ["", _bold("  [3/4] Step Functions — CSV Pipeline (last 5)"), ""]
    sm_arn = (
        f"arn:aws:states:{REGION}:{ACCOUNT_ID}:stateMachine"
        f":{NAME_PREFIX}-{env}-csv-pipeline"
    )
    res = c.run(
        f"aws stepfunctions list-executions --state-machine-arn {sm_arn} "
        f"--max-results 5 --region {REGION} --output json",
        hide=True,
        warn=True,
    )
    if res.ok:
        executions = json.loads(res.stdout).get("executions", [])
        if executions:
            sf_rows = []
            for ex in executions:
                status = ex.get("status", "?")
                if status == "SUCCEEDED":
                    status_col = _green(status)
                elif status in ("FAILED", "TIMED_OUT", "ABORTED"):
                    status_col = _red(status)
                else:
                    status_col = _yellow(status)
                start = ex.get("startDate", "?")
                if isinstance(start, (int, float)):
                    start = datetime.datetime.fromtimestamp(start).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                elif isinstance(start, str):
                    start = start[:19].replace("T", " ")
                sf_rows.append([ex.get("name", "?")[-40:], status_col, start])
            table = _tabulate(
                sf_rows,
                headers=["Execution", "Status", "Started"],
                tablefmt="simple",
                disable_numparse=True,
            )
            for line in table.splitlines():
                lines.append(f"  {line}")
        else:
            lines.append("  No executions found.")
    else:
        lines.append(f"  {_red('State machine not found.')} ARN: {sm_arn}")

    # --- 4. Bedrock model access ---
    lines += ["", _bold("  [4/4] Bedrock Model Access"), ""]
    seen: dict[str, tuple[bool, str]] = {}
    bedrock_rows = []
    for model_ids in _BEDROCK_MODELS.values():
        for model_id in model_ids:
            if model_id not in seen:
                seen[model_id] = _bedrock_probe(model_id)
            ok, msg = seen[model_id]
            bedrock_rows.append([model_id, _green(msg) if ok else _red(msg)])
    table = _tabulate(
        bedrock_rows,
        headers=["Model ID", "Status"],
        tablefmt="simple",
        disable_numparse=True,
    )
    for line in table.splitlines():
        lines.append(f"  {line}")

    lines += ["", _SEP, ""]
    print("\n".join(lines))
    log_path = _log_write(f"env-health-{env}", lines)
    print(f"  Log saved → {log_path.relative_to(REPO_ROOT)}")


@task(help={"env": "Environment name (dev|qa|prod|dev-<user>)."})
def endpoints(c, env):
    """Show and save all live endpoints for an environment.

    Reads CloudFormation stack Outputs and prints a friendly table including
    the live Amplify frontend URL. Also writes endpoints-<env>.md in the repo root.
    """
    _ensure_aws(c)
    outputs, status = _endpoints_data(c, env)
    if not outputs:
        status_msg = f" (StackStatus: {status})" if status else ""
        raise SystemExit(
            f"  [!] Stack '{NAME_PREFIX}-{env}' sin outputs{status_msg}.\n"
            "      Si el stack no existe: `uv run fab env.up {env}`\n"
            "      Si está en ROLLBACK_COMPLETE: `uv run fab env.up {env}` (auto-recover)."
        )
    frontend_url = _amplify_frontend_url(c, env)
    # Build docs URL from CFN output (already fetched) — avoids a second list-apps call
    docs_app_id = next(
        (o["OutputValue"] for o in outputs if o["OutputKey"] == "AmplifyDocsAppId"), ""
    )
    docs_url = f"https://demo.{docs_app_id}.amplifyapp.com" if docs_app_id else ""
    _show_and_save_endpoints(env, outputs, frontend_url=frontend_url, docs_url=docs_url)


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
    help={"env": "Environment name (dev|qa|prod|dev-<user>). Default: dev"},
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
            # Synchronous (RequestResponse) invokes wait for the whole Lambda
            # run. Large PDFs take longer than the AWS CLI's default 60s read
            # timeout, so a slow-but-succeeding Lambda looks like a client
            # "read timeout". Disable the read timeout (0 = wait; capped in
            # practice by the Lambda's own 900s ceiling) on the sync path only —
            # the async path returns 202 immediately and needs no override.
            if async_:
                extra = "--invocation-type Event"
            else:
                extra = "--log-type Tail --cli-read-timeout 0 --cli-connect-timeout 10"
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
        "parallel": "For csv: start all Step Functions executions concurrently (one per S3 CSV) and wait for all to complete. Without this flag, processes files sequentially.",
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
        failed: list[str] = []
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
                    failed.append(key)
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
                        if status != "SUCCEEDED":
                            failed.append(key)
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
        return len(keys) - len(failed), len(failed)

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
        summary_rows: list[tuple[str, str, str]] = []
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
                # See invoke(): disable the CLI read timeout on the synchronous
                # path so large PDFs that outlast the default 60s don't surface
                # as a spurious client-side "read timeout".
                if async_:
                    extra = "--invocation-type Event"
                else:
                    extra = (
                        "--log-type Tail --cli-read-timeout 0 --cli-connect-timeout 10"
                    )
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
                async_suffix = _dim(" (async)") if async_ else ""
                print(f"    {icon} {key}{async_suffix}")
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
                summary_rows.append((Path(key).name, status, "OK" if r.ok else "ERROR"))
            finally:
                os.unlink(payload_path)
                os.unlink(out_path)

        # Summary table
        from tabulate import tabulate as _tabulate

        print(f"\n  {'─' * 56}")
        print(f"  Resumen — {len(keys)} PDF(s)  env={env}")
        print(f"  {'─' * 56}")
        table = _tabulate(
            summary_rows,
            headers=["Archivo", "Status", "Resultado"],
            tablefmt="simple",
            disable_numparse=True,
        )
        for line in table.splitlines():
            print(f"  {line}")
        print(f"  {'─' * 56}\n")

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
        return len(keys) - len(failed), len(failed)

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
    "Lambda PDF (classification — Haiku 4.5)": [
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",  # PDF_HAIKU_MODEL_ID
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


# Maps friendly pipeline names to (lambda_suffix, env_var_key).
_PIPELINE_MODEL_VARS: dict[str, tuple[str, str]] = {
    "api": ("api", "RAG_CLAUDE_MODEL_ID"),
    "api-nova": ("api", "RAG_NOVA_MODEL_ID"),
    "api-deepseek": ("api", "RAG_DEEPSEEK_MODEL_ID"),
    "csv": ("csv", "BEDROCK_MODEL_ID"),
    "csv-embed": ("csv", "BEDROCK_EMBED_MODEL_ID"),
    "pdf": ("pdf", "PDF_CLAUDE_MODEL_ID"),
    "pdf-haiku": ("pdf", "PDF_HAIKU_MODEL_ID"),
    "pdf-embed": ("pdf", "PDF_TITAN_MODEL_ID"),
}


@task(
    positional=["pipeline", "env", "model_id"],
    help={
        "pipeline": ("Pipeline alias: " + " | ".join(_PIPELINE_MODEL_VARS) + "."),
        "env": "Environment name (dev|qa|prod|dev-<user>).",
        "model_id": "Bedrock model ID to set (e.g. us.anthropic.claude-sonnet-4-6).",
    },
)
def set_model(c, pipeline, env, model_id):
    """Shortcut to change a pipeline's Bedrock model without knowing the env var name.

    Resolves the correct Lambda function and environment variable for the given
    pipeline alias, then delegates to lambda.set-env.

    Examples:
      uv run fab bedrock.set-model csv dev us.anthropic.claude-sonnet-4-6
      uv run fab bedrock.set-model pdf-haiku dev us.anthropic.claude-haiku-4-5-20251001-v1:0
      uv run fab bedrock.set-model api dev us.amazon.nova-pro-v1:0
    """
    if pipeline not in _PIPELINE_MODEL_VARS:
        valid = ", ".join(_PIPELINE_MODEL_VARS)
        raise SystemExit(f"Unknown pipeline '{pipeline}'. Valid aliases: {valid}")
    lambda_suffix, env_var = _PIPELINE_MODEL_VARS[pipeline]
    set_env(c, lambda_suffix, env, env_var, model_id)


# --------------------------------------------------------------------------- #
# opensearch.* — collection status and index doc counts
# --------------------------------------------------------------------------- #
OPENSEARCH_INDICES = ["csv_telemetry_vecs", "pdf_legal_vecs", "analysis_vecs"]


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


@task(help={"env": "Environment name (dev|qa|prod|dev-<user>). Default: dev"})
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
    outputs, _ = _endpoints_data(c, env)
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
        "env": "Environment name (dev|qa|prod|dev-<user>).",
        "confirm": "Skip the confirmation prompt (use in scripts).",
    },
)
def reindex(c, env, confirm=False):
    """Re-ingest all CSVs (parallel) and PDFs (serial) into OpenSearch from S3.

    Pipelines upsert by document ID so re-running overwrites existing documents
    without creating duplicates. Use this after a stack re-creation or when the
    OpenSearch collection is empty / corrupted.

    Examples:
      uv run fab opensearch.reindex dev
      uv run fab opensearch.reindex dev --confirm   # non-interactive / CI
    """
    _ensure_aws(c)
    if not confirm:
        print(
            f"\n  Will re-ingest ALL CSVs (parallel) + ALL PDFs (serial) into '{env}'."
        )
        print(f"  Type '{env}' to confirm: ", end="", flush=True)
        if input().strip() != env:
            raise SystemExit("Aborted.")
    print(f"\n==> [reindex] Re-ingesting CSVs (parallel) into '{env}' ...")
    invoke_all(c, "csv", env, parallel=True)
    print(f"\n==> [reindex] Re-ingesting PDFs (serial) into '{env}' ...")
    invoke_all(c, "pdf", env)
    print(
        f"\n==> [reindex] Done. Run `fab opensearch.status {env}` to verify doc counts."
    )


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
            has_timeout = any("Task timed out" in m for m in msgs)
            has_report = any("REPORT RequestId" in m for m in msgs)
            is_skip = any("Skipping non-PDF object" in m for m in msgs)
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

            has_success_evidence = file_key is not None
            if has_timeout:
                status = "timeout"
            elif has_error or no_sections:
                status = "error"
            elif has_success_evidence or is_skip:
                status = "ok"
            elif has_report:
                # REPORT line present (invocation finished) but no error, timeout,
                # or success evidence — likely a Lambda hard-kill that didn't emit
                # "Task timed out" in time to be captured, or an untraced failure.
                status = "unknown"
            else:
                # No REPORT line yet — still in-flight within this query window.
                status = "ok"
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
        timeout_count = sum(1 for i in invocations if i["status"] == "timeout")
        unknown_count = sum(1 for i in invocations if i["status"] == "unknown")

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
            elif inv["status"] == "timeout":
                print(f"  {_yellow('TIMEOUT')}  {_dim(inv['ts'])}  {label}{_dim(dur)}")
                if verbose:
                    print(
                        _dim(
                            "       Lambda hit its hard timeout ceiling before completing"
                        )
                    )
            elif inv["status"] == "unknown":
                print(f"  {_dim('UNKNOWN')}  {_dim(inv['ts'])}  {label}{_dim(dur)}")
                if verbose:
                    print(
                        _dim(
                            "       REPORT line present but no success/error/timeout evidence"
                        )
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
        if (
            err_count == 0
            and timeout_count == 0
            and unknown_count == 0
            and ok_count > 0
        ):
            print(_green(f"  All {ok_count} invocation(s) completed successfully."))
        else:
            status_str = (
                _green(f"{ok_count} ok")
                + "  "
                + (_red(f"{err_count} failed") if err_count else _dim("0 failed"))
                + "  "
                + (
                    _yellow(f"{timeout_count} timed out")
                    if timeout_count
                    else _dim("0 timed out")
                )
                + "  "
                + (
                    _dim(f"{unknown_count} unknown")
                    if unknown_count
                    else _dim("0 unknown")
                )
            )
            total = ok_count + err_count + timeout_count + unknown_count
            print(f"  {total} invocation(s) total  —  {status_str}")
            if (err_count or timeout_count or unknown_count) and not verbose:
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
        "    or @message like /Task timed out/ or @message like /Skipping non-PDF object/ "
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
# step-functions.* — execution history for the CSV pipeline state machine
# --------------------------------------------------------------------------- #


@task(
    positional=["env"],
    help={
        "env": "Environment name (dev|qa|prod|dev-<user>).",
        "n": "Number of executions to show (default: 10).",
    },
)
def sf_history(c, env, n=10):
    """List the N most recent Step Functions executions for the CSV pipeline.

    Shows execution name (last 8 chars), status, start time, and duration.

    Examples:
      uv run fab step-functions.history dev
      uv run fab step-functions.history dev --n 20
    """
    import datetime as _dt

    from tabulate import tabulate as _tabulate

    _ensure_aws(c)
    arn = (
        f"arn:aws:states:{REGION}:{ACCOUNT_ID}:stateMachine:"
        f"{NAME_PREFIX}-{env}-csv-pipeline"
    )
    res = c.run(
        f"aws stepfunctions list-executions --state-machine-arn {arn} "
        f"--max-results {n} --output json --region {REGION}",
        hide=True,
        warn=True,
    )
    if not res.ok:
        raise SystemExit(f"Could not list executions: {res.stderr.strip()[:120]}")

    executions = json.loads(res.stdout).get("executions", [])
    if not executions:
        print(f"  No executions found for {NAME_PREFIX}-{env}-csv-pipeline.")
        return

    _STATUS_COLOR = {
        "SUCCEEDED": _green,
        "FAILED": _red,
        "TIMED_OUT": _red,
        "ABORTED": _yellow,
        "RUNNING": _yellow,
    }

    rows = []
    for ex in executions:
        name = ex.get("name", "")[-8:]
        status = ex.get("status", "UNKNOWN")
        start_raw = ex.get("startDate", "")
        stop_raw = ex.get("stopDate", "")

        # Parse ISO timestamps (may include fractional seconds or 'Z')
        def _parse_ts(s):
            s = s.replace("Z", "+00:00")
            try:
                return _dt.datetime.fromisoformat(s)
            except ValueError:
                return None

        start_dt = _parse_ts(start_raw)
        stop_dt = _parse_ts(stop_raw)
        start_str = (
            start_dt.strftime("%Y-%m-%d %H:%M:%S") if start_dt else start_raw[:19]
        )

        if start_dt and stop_dt:
            secs = int((stop_dt - start_dt).total_seconds())
            duration = f"{secs // 60}m {secs % 60}s"
        elif status == "RUNNING":
            duration = "running…"
        else:
            duration = "—"

        color = _STATUS_COLOR.get(status, lambda x: x)
        rows.append([name, color(status), start_str, duration])

    table = _tabulate(
        rows,
        headers=["Name (last 8)", "Status", "Started", "Duration"],
        tablefmt="simple",
        disable_numparse=True,
    )
    print()
    print(_bold(f"  Step Functions — {NAME_PREFIX}-{env}-csv-pipeline"))
    print()
    for line in table.splitlines():
        print(f"  {line}")
    print()


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
env.add_task(health)

frontend_ns = Collection("frontend")
frontend_ns.add_task(deploy)
frontend_ns.add_task(validate)

ollama = Collection("ollama")
ollama.add_task(health_check)
ollama.add_task(restart_ollama)
ollama.add_task(pull_model)
ollama.add_task(logs)


@task(
    help={
        "name": "Lambda suffix: api | csv | pdf.",
        "env": "Environment name (dev|qa|prod|dev-<user>).",
        "publish": "Publish a new version after the code update and point alias 'live' to it.",
    },
)
def redeploy(c, name, env, publish=False):
    """Re-zip and push Lambda code without rebuilding layers or running a full CFN deploy.

    Zips backend/ (skipping .lambda-layers/, __pycache__, *.pyc, .git/) and calls
    aws lambda update-function-code. Useful after editing handler code when the
    layer dependencies have not changed.

    With --publish: also publishes a new numbered version and updates (or creates)
    the alias 'live' to point to it. Required before lambda.rollback can roll back.

    Examples:
      uv run fab lambda.redeploy api dev
      uv run fab lambda.redeploy api dev --publish
    """
    _ensure_aws(c)
    fn_name = _lambda_fn(env, name)
    src = TARGET_ROOT / "backend"
    if not src.is_dir():
        raise SystemExit(f"backend/ not found: {src}")

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        zip_path = tmp.name

    EXCLUDE_DIRS = {
        ".lambda-layers",
        "__pycache__",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
    }
    EXCLUDE_EXTS = {".pyc", ".pyo"}

    try:
        print(f"==> [redeploy] Zipping {src} ...")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in src.rglob("*"):
                if not f.is_file():
                    continue
                if any(part in EXCLUDE_DIRS for part in f.parts):
                    continue
                if f.suffix in EXCLUDE_EXTS:
                    continue
                zf.write(f, f.relative_to(src))
        size_mb = round(os.path.getsize(zip_path) / (1024 * 1024), 1)
        print(f"==> [redeploy] zip ready: {size_mb} MB — uploading to {fn_name} ...")
        publish_flag = " --publish" if publish else ""
        res = c.run(
            f"aws lambda update-function-code --function-name {fn_name} "
            f'--zip-file "fileb://{zip_path}" --region {REGION}{publish_flag} '
            '--query "[FunctionName,LastModified,CodeSize,Version]" --output text',
            hide=False,
            warn=True,
        )
        if not res.ok:
            raise SystemExit(f"[redeploy] update-function-code failed: {res.stderr}")
        print(_green(f"==> [redeploy] {fn_name} updated successfully"))
        if publish:
            version = res.stdout.strip().split()[-1]
            # Update alias 'live'; create it if it doesn't exist yet
            alias_res = c.run(
                f"aws lambda update-alias --function-name {fn_name} --name live "
                f"--function-version {version} --region {REGION} --output text",
                hide=True,
                warn=True,
            )
            if not alias_res.ok:
                c.run(
                    f"aws lambda create-alias --function-name {fn_name} --name live "
                    f"--function-version {version} --region {REGION} --output text",
                    hide=True,
                )
            print(_green(f"==> [redeploy] alias 'live' → version {version}"))
    finally:
        os.unlink(zip_path)


@task(
    positional=["name", "env"],
    help={
        "name": "Lambda suffix: api | csv | pdf.",
        "env": "Environment name (dev|qa|prod|dev-<user>).",
        "version": "Version number to activate. Omit to list available versions.",
    },
)
def rollback(c, name, env, version=None):
    """List published Lambda versions or roll back via alias 'live'.

    Requires versions to have been published first:
      uv run fab lambda.redeploy <name> <env> --publish

    Examples:
      uv run fab lambda.rollback api dev             # list published versions
      uv run fab lambda.rollback api dev --version 3 # point alias 'live' to version 3
    """
    from tabulate import tabulate as _tabulate

    _ensure_aws(c)
    fn_name = _lambda_fn(env, name)

    r = c.run(
        f"aws lambda list-versions-by-function --function-name {fn_name} "
        f"--region {REGION} --output json",
        hide=True,
        warn=True,
    )
    if not r.ok:
        raise SystemExit(f"[rollback] failed to list versions: {r.stderr}")

    versions = [
        v for v in json.loads(r.stdout).get("Versions", []) if v["Version"] != "$LATEST"
    ]
    if not versions:
        raise SystemExit(
            f"[rollback] no published versions found for {fn_name}.\n"
            f"  Publish first: uv run fab lambda.redeploy {name} {env} --publish"
        )

    alias_r = c.run(
        f"aws lambda get-alias --function-name {fn_name} --name live "
        f"--region {REGION} --output json",
        hide=True,
        warn=True,
    )
    current_ver = (
        json.loads(alias_r.stdout).get("FunctionVersion", "(none)")
        if alias_r.ok
        else "(no alias)"
    )

    if version is None:
        rows = [
            [
                v["Version"],
                v.get("LastModified", "")[:19],
                "<-- live" if v["Version"] == current_ver else "",
            ]
            for v in sorted(versions, key=lambda x: int(x["Version"]), reverse=True)
        ]
        print()
        print(
            _bold(f"  Published versions — {fn_name}  (alias 'live' → {current_ver})")
        )
        print()
        for line in _tabulate(
            rows, headers=["Version", "Published", ""], tablefmt="simple"
        ).splitlines():
            print(f"  {line}")
        print()
        print(f"  To roll back: uv run fab lambda.rollback {name} {env} --version N")
        return

    version = str(version)
    valid = {v["Version"] for v in versions}
    if version not in valid:
        raise SystemExit(
            f"[rollback] version {version} not found. Available: {', '.join(sorted(valid, key=int))}"
        )

    upd = c.run(
        f"aws lambda update-alias --function-name {fn_name} --name live "
        f"--function-version {version} --region {REGION} --output text",
        hide=True,
        warn=True,
    )
    if not upd.ok:
        c.run(
            f"aws lambda create-alias --function-name {fn_name} --name live "
            f"--function-version {version} --region {REGION} --output text",
            hide=True,
        )
    print(_green(f"==> [rollback] alias 'live' on {fn_name} → version {version}"))


# --------------------------------------------------------------------------- #
# docs.* — mkdocs-material build and Amplify deploy
# --------------------------------------------------------------------------- #
DOCS_DIR = REPO_ROOT / "docs"


def _docs_content_hash() -> str:
    """SHA-256 of all docs source files + mkdocs.yml.

    Used to skip Amplify uploads when nothing has changed since the last deploy.
    """
    import hashlib

    h = hashlib.sha256()
    sources = sorted(
        [*DOCS_DIR.rglob("*.md"), *DOCS_DIR.rglob("*.png"), REPO_ROOT / "mkdocs.yml"],
        key=lambda p: str(p),
    )
    for path in sources:
        if path.is_file():
            h.update(str(path.relative_to(REPO_ROOT)).encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def _amplify_docs_app_id(c, env):
    """Return the Amplify docs app ID from CFN stack outputs (AmplifyDocsAppId)."""
    outputs, status = _endpoints_data(c, env)
    app_id = next(
        (o["OutputValue"] for o in outputs if o["OutputKey"] == "AmplifyDocsAppId"), ""
    )
    if not app_id:
        raise SystemExit(
            f"AmplifyDocsAppId not found in stack outputs for env={env} (status={status}). "
            "Run `fab env.up <env> --skip-frontend` first to create the infrastructure."
        )
    return app_id


@task(positional=["env"])
def docs_build(c, env="dev"):
    """Build the mkdocs-material documentation site into site/."""
    c.run(f'"{_mkdocs_bin()}" build --strict', warn=False)
    print("==> [docs] Built to site/")


@task(
    positional=["env"],
    help={
        "env": "Environment name (dev|qa|prod). Default: dev.",
        "force": "Deploy even if no source files changed since the last deploy.",
    },
)
def docs_deploy(c, env, force=False):
    """Build the mkdocs site and push it to the docs Amplify app.

    Skips the Amplify upload if no docs source files (*.md, *.png, mkdocs.yml)
    have changed since the last successful deploy. Use --force to override.

    Requires the AmplifyDocs stack to exist:
      uv run fab env.up dev --skip-frontend

    Examples:
      uv run fab docs.deploy dev
      uv run fab docs.deploy dev --force
    """
    import time as _time

    _ensure_aws(c)

    # --- Change detection ---
    LOGS_DIR.mkdir(exist_ok=True)
    hash_file = LOGS_DIR / f"docs-deploy-{env}.hash"
    current_hash = _docs_content_hash()
    last_hash = (
        hash_file.read_text(encoding="utf-8").strip() if hash_file.exists() else ""
    )

    if not force and current_hash == last_hash:
        print(
            f"==> [docs] No changes detected since last deploy (env={env}). "
            "Skipping. Use --force to deploy anyway."
        )
        return

    print(f"==> docs.deploy: env={env} region={REGION}  {'(forced)' if force else ''}")

    # --- Build ---
    print("==> [docs] Building documentation...")
    mkdocs_bin = _mkdocs_bin()
    result = c.run(f'"{mkdocs_bin}" build --strict', hide=True, warn=True)
    if not result.ok:
        raise SystemExit(f"[docs] Build failed:\n{result.stderr}")
    dist_dir = REPO_ROOT / "site"
    if not (dist_dir / "index.html").exists():
        raise SystemExit(f"[docs] Build failed — {dist_dir}/index.html not found")
    print("==> [docs] Build OK")

    # --- Upload ---
    ts = _time.strftime("%Y%m%d-%H%M%S")
    log_path = LOGS_DIR / f"docs-deploy-{env}-{ts}.log"
    lines: list[str] = []

    def _log(msg):
        print(msg)
        lines.append(msg)

    app_id = _amplify_docs_app_id(c, env)
    url = _amplify_upload_and_poll(c, env, dist_dir, _log, app_id=app_id)
    log_path.write_text("\n".join(lines), encoding="utf-8")

    # --- Save hash only on successful deploy ---
    hash_file.write_text(current_hash, encoding="utf-8")

    print(f"==> [docs] Live: {url}")
    print(f"==> [docs] Log: {log_path}")


@task(
    positional=["env"],
    help={
        "env": "Environment name (dev|qa|prod).",
        "clients": "Comma-separated client list to ingest (default: all discovered clients).",
        "force": "Re-ingest even if ledger says up to date.",
    },
)
def analysis_ingest(c, env, clients=None, force=False):
    """Ingest all data-analysis results into the analysis_vecs OpenSearch index.

    Runs the orchestrator CLI which:
    - Discovers all client folders in the telemetry bucket (or --clients subset)
    - Runs FolderPipeline (Strands agent) per client to analyze their telemetry
    - Embeds findings chunks and bulk-indexes into analysis_vecs
    - Tracks progress in a ledger (S3 NDJSON) to skip re-ingest if unchanged

    Returns (ok_count, fail_count) tuple.
    Raises SystemExit on CLI failure (exit code 1).
    """
    import json as _json

    _ensure_aws(c)

    # Get OPENSEARCH_HOST and FLEET_S3_BUCKET from CFN outputs
    outputs, _ = _endpoints_data(c, env)
    os_endpoint = next(
        (
            o["OutputValue"]
            for o in (outputs or [])
            if o["OutputKey"] == "OpenSearchEndpoint"
        ),
        "",
    )
    # Strip the https:// scheme — the pipeline OPENSEARCH_HOST wants host only
    os_host = os_endpoint.replace("https://", "").rstrip("/") if os_endpoint else ""
    fleet_bucket = next(
        (
            o["OutputValue"]
            for o in (outputs or [])
            if o["OutputKey"] == "TelemetryDataBucket"
        ),
        f"{NAME_PREFIX}-{env}-telemetry-data",  # fallback to convention
    )

    if not os_host:
        raise SystemExit(
            f"OpenSearchEndpoint not found in {NAME_PREFIX}-{env} stack outputs. "
            "Run `fab env.up {env}` to create the stack first."
        )

    # Build the orchestrator CLI command
    target = f"--clients {clients}" if clients else "--all"
    extra = " --force" if force else ""

    cmd = (
        f"cd onprem-aws/backend && "
        f"OPENSEARCH_HOST={os_host} "
        f"FLEET_S3_BUCKET={fleet_bucket} "
        f"uv run --with-requirements requirements.txt "
        f"python -m data_analysis_agent.agent.ingest_orchestrator {target}{extra}"
    )

    print(f"==> [analysis] Running ingest orchestrator (env={env})...")
    result = c.run(cmd, hide=False, warn=True)

    if not result.ok:
        raise SystemExit(
            f"[analysis] Orchestrator exited {result.return_code}. "
            f"Check logs above for details."
        )

    # Parse JSON output to extract counts
    try:
        lines = result.stdout.splitlines()
        # Find the line that starts with '[' (JSON array start)
        json_start = next(
            i for i, line in enumerate(lines) if line.strip().startswith("[")
        )
        # Join all lines from json_start to end
        json_text = "\n".join(lines[json_start:])
        results = _json.loads(json_text)
        ok = sum(1 for r in results if r.get("action") == "indexed")
        fail = sum(1 for r in results if r.get("action") == "error")
        skipped = sum(1 for r in results if r.get("action") == "skipped")
        no_files = sum(1 for r in results if r.get("action") == "no_files")
        print(
            f"==> [analysis] Complete: {ok} indexed, {fail} failed, "
            f"{skipped} skipped, {no_files} no files"
        )
        return ok, fail
    except (ValueError, IndexError, KeyError, StopIteration):
        # Fallback: assume success if exit code was 0
        print("[warn] [analysis] Could not parse JSON output; assuming success")
        return 1, 0


lambda_ns = Collection("lambda")
lambda_ns.add_task(pull)
lambda_ns.add_task(build_layer, name="build-layer")
lambda_ns.add_task(redeploy)
lambda_ns.add_task(rollback)
lambda_ns.add_task(invoke)
lambda_ns.add_task(invoke_all, name="invoke-all")
lambda_ns.add_task(pdf_async_status, name="pdf-async-status")
lambda_ns.add_task(set_env, name="set-env")
lambda_ns.add_task(lambda_logs, name="logs")
lambda_ns.add_task(lambda_status, name="status")

bedrock_ns = Collection("bedrock")
bedrock_ns.add_task(model_access, name="model-access")
bedrock_ns.add_task(set_model, name="set-model")

opensearch_ns = Collection("opensearch")
opensearch_ns.add_task(opensearch_status, name="status")
opensearch_ns.add_task(reindex)

sfn_ns = Collection("step-functions")
sfn_ns.add_task(sf_history, name="history")

docs_ns = Collection("docs")
docs_ns.add_task(docs_build, name="build")
docs_ns.add_task(docs_deploy, name="deploy")

analysis_ns = Collection("analysis")
analysis_ns.add_task(analysis_ingest, name="ingest")

ns = Collection()
ns.add_collection(env)
ns.add_collection(frontend_ns)
ns.add_collection(ollama)
ns.add_collection(lambda_ns)
ns.add_collection(bedrock_ns)
ns.add_collection(opensearch_ns)
ns.add_collection(sfn_ns)
ns.add_collection(docs_ns)
ns.add_collection(analysis_ns)
