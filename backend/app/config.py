from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        extra="ignore",
    )

    ollama_url: str
    model: str
    cors_origins: str
    database_path: Path
    allowed_dirs: str
    system_prompt: str
    embedding_model: str
    chroma_path: Path
    whisper_model: str = "base.en"
    whisper_language: str = "en"
    piper_voice_path: Path = Path("data/voices/en_GB-alan-medium.onnx")
    approval_timeout_seconds: int = 300

    @field_validator("ollama_url")
    @classmethod
    def validate_local_ollama_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        is_loopback = hostname == "localhost"
        if hostname is not None:
            try:
                is_loopback = is_loopback or ip_address(hostname).is_loopback
            except ValueError:
                pass
        if parsed.scheme != "http" or not is_loopback:
            raise ValueError("OLLAMA_URL must point to an HTTP loopback address")
        return value.rstrip("/")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def resolved_database_path(self) -> Path:
        if self.database_path.is_absolute():
            return self.database_path
        return (Path(__file__).resolve().parents[1] / self.database_path).resolve()

    @property
    def resolved_chroma_path(self) -> Path:
        if self.chroma_path.is_absolute():
            return self.chroma_path
        return (Path(__file__).resolve().parents[1] / self.chroma_path).resolve()

    @property
    def resolved_piper_voice_path(self) -> Path:
        if self.piper_voice_path.is_absolute():
            return self.piper_voice_path
        return (Path(__file__).resolve().parents[1] / self.piper_voice_path).resolve()

    @property
    def resolved_allowed_dirs(self) -> list[Path]:
        backend_dir = Path(__file__).resolve().parents[1]
        directories = []
        for value in self.allowed_dirs.split(";"):
            normalized = value.strip()
            if not normalized:
                continue
            directory = Path(normalized).expanduser()
            if not directory.is_absolute():
                directory = backend_dir / directory
            directories.append(directory.resolve())
        return directories


@lru_cache
def get_settings() -> Settings:
    return Settings()
