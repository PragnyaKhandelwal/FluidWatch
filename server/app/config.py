import secrets
from pathlib import Path

from dotenv import set_key
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/iv_drip.db"
    api_auth_token: str = ""
    cors_origins: str = "*"

    # Training-data archive (app/dataset_archive.py) - off unless both are
    # set. The service-role key bypasses RLS: server-side only, never in
    # the dashboard bundle or the firmware.
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    dataset_bucket: str = "training-frames"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()

if not settings.api_auth_token:
    # No opt-in "leave it open" flag - an unset token used to mean anyone on
    # the LAN could POST fake readings. Generate one and persist it to .env
    # so the API is authenticated by default with zero manual setup, and the
    # token stays stable across restarts.
    settings.api_auth_token = secrets.token_urlsafe(24)
    _ENV_PATH.touch(exist_ok=True)
    set_key(str(_ENV_PATH), "API_AUTH_TOKEN", settings.api_auth_token)
    print(
        "[config] No API_AUTH_TOKEN was set - generated one and saved it to "
        f"{_ENV_PATH}.\n"
        "Copy this same value into the ESP32 firmware's secrets.h "
        "(API_AUTH_TOKEN) so it can still authenticate:\n"
        f"    API_AUTH_TOKEN={settings.api_auth_token}"
    )
