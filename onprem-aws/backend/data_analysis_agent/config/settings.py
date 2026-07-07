"""
Central configuration. Values are read from environment variables so
nothing sensitive is ever hard-coded.
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

from data_analysis_agent.config.opensearch_settings import OpenSearchConfig

load_dotenv()


@dataclass
class S3Config:
    bucket_name: str = field(default_factory=lambda: os.getenv(
        "FLEET_S3_BUCKET", "bhitech-minelogx-poc-telemetry-data"
    ))
    region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    prefix: str = field(default_factory=lambda: os.getenv("FLEET_S3_PREFIX", ""))


@dataclass
class OllamaConfig:
    endpoint: str = field(default_factory=lambda: os.getenv(
        "OLLAMA_ENDPOINT", "http://ec2-98-81-228-187.compute-1.amazonaws.com:11434"
    ))
    model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen3:8b"))
    max_tokens: int = 4096
    max_agent_turns: int = 20


@dataclass
class BedrockConfig:
    region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    # Cross-region inference profile for Claude Sonnet 4.6.
    # Override with BEDROCK_MODEL_ID if the exact version string differs in your account.
    model_id: str = field(default_factory=lambda: os.getenv(
        "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"
    ))
    max_tokens: int = 8096
    max_agent_turns: int = 20


@dataclass
class AgentConfig:
    s3: S3Config = field(default_factory=S3Config)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    bedrock: BedrockConfig = field(default_factory=BedrockConfig)
    opensearch: OpenSearchConfig = field(default_factory=OpenSearchConfig)
    cache_ttl_seconds: int = 300
    local_data_path: str = "sample_data"


# Singleton — import this everywhere
settings = AgentConfig()
