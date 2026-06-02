"""
Central configuration. Values are read from environment variables so
nothing sensitive is ever hard-coded.
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class S3Config:
    bucket_name: str = field(default_factory=lambda: os.getenv("FLEET_S3_BUCKET", ""))
    region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    prefix: str = field(default_factory=lambda: os.getenv("FLEET_S3_PREFIX", "fleet/"))
    # AWS credentials come from the standard boto3 chain:
    # env vars → ~/.aws/credentials → IAM role. Never store them here.


@dataclass
class AnthropicConfig:
    model: str = "claude-sonnet-4"
    max_tokens: int = 4096
    max_agent_turns: int = 20           # hard safety cap on the agentic loop


@dataclass
class AgentConfig:
    s3: S3Config = field(default_factory=S3Config)
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    bedrock: BedrockConfig = field(default_factory=BedrockConfig)
    cache_ttl_seconds: int = 300        # how long parsed DataFrames are cached
    local_data_path: str = "sample_data"  # fallback for dev/testing

@dataclass
class BedrockConfig:
    region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    model_id: str = field(default_factory=lambda: os.getenv(
        "BEDROCK_MODEL_ID",
        "us.anthropic.claude-sonnet-4-20250514-v1:0"   # Bedrock uses its own model ID namespace
    ))

# Singleton — import this everywhere
settings = AgentConfig()
