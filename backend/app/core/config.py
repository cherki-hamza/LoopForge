from pathlib import Path
from typing import List, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "LoopForge"
    API_V1_PREFIX: str = "/api"

    # Security
    JWT_SECRET: str = "loopforge-super-secret-jwt-key-2026-production-ready"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Admin credentials
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "loopforge2026"

    # Database & Redis
    DATABASE_URL: str = "postgresql://loopforge:loopforge@postgres:5432/loopforge"
    REDIS_URL: str = "redis://redis:6379/0"

    # Storage Directories
    STORAGE_DIR: str = str(Path(__file__).resolve().parent.parent.parent.parent / "storage")
    
    # Limits & Configuration
    MAX_FILE_SIZE_MB: int = 500
    MAX_CLIP_DURATION_SECONDS: float = 120.0
    MAX_CONCURRENT_JOBS: int = 10
    RETENTION_DAYS: int = 14
    JOB_TIMEOUT_SECONDS: int = 7200  # 2 hours per job max

    # CORS
    CORS_ORIGINS: Union[str, List[str]] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @property
    def uploads_dir(self) -> Path:
        p = Path(self.STORAGE_DIR) / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def outputs_dir(self) -> Path:
        p = Path(self.STORAGE_DIR) / "outputs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def temp_dir(self) -> Path:
        p = Path(self.STORAGE_DIR) / "temp"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_FILE_SIZE_MB * 1024 * 1024

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )


settings = Settings()
