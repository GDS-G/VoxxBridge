from __future__ import annotations

from ipaddress import ip_address
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    voxbridge_transport: Literal["stdio", "streamable-http"] = "streamable-http"
    voxbridge_host: str = Field(default="127.0.0.1", min_length=1)
    voxbridge_port: int = Field(default=8000, ge=1, le=65_535)
    voxbridge_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    voxbridge_allow_remote_bind: bool = False
    voxbridge_max_text_chars: int = Field(default=3_000, ge=1, le=50_000)
    voxbridge_max_audio_bytes: int = Field(default=20 * 1024 * 1024, ge=1_024)
    voxbridge_max_concurrent_generations: int = Field(default=2, ge=1, le=64)
    voxbridge_request_timeout_seconds: float = Field(default=60.0, gt=0, le=300.0)
    voxbridge_allowed_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    )
    voxbridge_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ]
    )

    elevenlabs_api_key: str | None = None
    hume_api_key: str | None = None
    cartesia_api_key: str | None = None
    cartesia_version: Literal["2026-08-14"] = "2026-08-14"
    resemble_api_key: str | None = None
    openai_api_key: str | None = None
    deepgram_api_key: str | None = None
    google_application_credentials: str | None = None
    google_cloud_project: str | None = None
    google_cloud_tts_enabled: bool = False
    azure_speech_key: str | None = None
    azure_speech_region: str | None = None

    def assert_safe_bind(self) -> None:
        """Fail closed unless a non-loopback listener was explicitly approved.

        The explicit override is intended only for a private network or Secure MCP
        Tunnel sidecar. It is not authentication and must not be used to expose the
        gateway directly to the public internet.
        """
        if self.voxbridge_transport != "streamable-http":
            return
        host = self.voxbridge_host.strip().lower()
        try:
            is_loopback = ip_address(host).is_loopback
        except ValueError:
            is_loopback = host == "localhost"
        if not is_loopback and not self.voxbridge_allow_remote_bind:
            raise RuntimeError(
                "Refusing a non-loopback listener without "
                "VOXBRIDGE_ALLOW_REMOTE_BIND=true. Use only behind a private "
                "network or Secure MCP Tunnel; public deployments require OAuth 2.1."
            )


settings = Settings()
