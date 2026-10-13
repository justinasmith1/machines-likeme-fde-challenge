from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # .env is looked up at the repo root and in backend/, whatever the working directory.
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Where the caller's identity comes from (app/api/deps.py):
    #   "development"  the X-Dev-User / X-Dev-Role headers. Local work only.
    #   "entra"        the identity Azure Container Apps injects after a Microsoft Entra ID
    #                  sign-in. Only for a deployment where the API is not reachable from
    #                  outside: the header is trusted because of where it comes from.
    #   anything else  every request gets 401.
    # The default is "production": with no configuration the system is closed, not open.
    # For local development set APP_ENV=development in .env.
    app_env: str = "production"
    # The only browser origin allowed to call the API (CORS).
    frontend_origin: str = "http://localhost:3000"

    # Azure OpenAI is optional so the app starts without it.
    # The key is a SecretStr: repr/str show '**********', never the value.
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_deployment: str = "JustinaSmith-RCS2"

    price_tolerance_pct: Decimal = Decimal("0.02")
    analyst_approval_limit: Decimal = Decimal("5000")

    reference_data_dir: str = "data/reference"
    database_url: str = "sqlite:///./contoso.db"

    # Extraction (phase 3)
    extraction_max_retries: int = 3  # retries after the first attempt, per call
    extraction_timeout_seconds: float = 90.0
    extraction_cache_dir: str = "data/cache/extractions"
    pdf_render_dpi: int = 170
    pdf_max_pages: int = 5
    max_upload_mb: int = 10

    # Uploaded documents (phase 4). Local folder now; Azure Blob Storage in deployment.
    uploads_dir: str = "data/uploads"

    @property
    def extraction_cache_path(self) -> Path:
        """Cache directory; a relative value is resolved against the repo root."""
        return _resolve(self.extraction_cache_dir)

    @property
    def uploads_path(self) -> Path:
        """Uploads directory; a relative value is resolved against the repo root."""
        return _resolve(self.uploads_dir)


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
