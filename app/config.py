from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    agent_backend: Literal["groq", "demo"] = "groq"
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "qwen/qwen3.8-27b"
    groq_plan_tokens: int = Field(default=4096, ge=512, le=8192)
    groq_section_tokens: int = Field(default=2048, ge=256, le=8192)
    groq_brief_section_tokens: int = Field(default=1024, ge=256, le=8192)
    llm_timeout_seconds: float = Field(default=180, ge=1, le=600)
    artifact_dir: Path = Path("artifacts")
