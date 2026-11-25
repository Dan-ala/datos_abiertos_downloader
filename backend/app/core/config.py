from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment or defaults."""

    app_name: str = "Datos Abiertos Downloader"
    default_domain: str = "www.datos.gov.co"
    downloads_dir: Path = Path(__file__).resolve().parents[2] / "downloads"
    max_concurrent_downloads: int = 3
    request_timeout_seconds: float = 120.0
    chunk_size_bytes: int = 65536
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    model_config = SettingsConfigDict(env_prefix="DAD_", env_file=".env", extra="ignore")


settings = Settings()
settings.downloads_dir.mkdir(parents=True, exist_ok=True)
