"""Environment-based configuration.

Secrets are read from the environment (or a local, git-ignored `.env`) and are never
logged or returned by any endpoint. `Settings.public_summary()` is the only view of the
configuration that may leave the process.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PAYPAL_SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"


class PaymentMode(StrEnum):
    """How payment operations are executed.

    SANDBOX   — real HTTP calls to the PayPal Sandbox REST API.
    REHEARSAL — no network calls; every result is generated locally and labeled
                SIMULATED in the API, the database and the UI. Never a silent fallback:
                rehearsal must be selected explicitly.
    """

    SANDBOX = "sandbox"
    REHEARSAL = "rehearsal"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://localhost/outcomeguard"

    payment_mode: PaymentMode = PaymentMode.SANDBOX
    paypal_client_id: str | None = None
    paypal_client_secret: SecretStr | None = None
    paypal_webhook_id: str | None = None
    # Production PayPal is intentionally not configurable in this build.
    paypal_base_url: str = PAYPAL_SANDBOX_BASE_URL
    paypal_timeout_seconds: float = 20.0

    # Where PayPal sends the buyer after approval / cancel. These are UX only; the
    # backend never trusts the redirect and always re-reads the order from PayPal.
    public_api_url: str = "http://localhost:8000"
    web_url: str = "http://localhost:3000"

    # The P02 sandbox-proof endpoints are a developer tool, off unless enabled.
    enable_sandbox_proof: bool = False
    # Shared secret required by operator-only endpoints until real auth lands (P06).
    operator_token: SecretStr | None = Field(default=None)
    # Signs session tokens. Must be stable across restarts (else everyone is logged out).
    session_secret: SecretStr | None = None

    # Verification (P04): the API hosts the controlled form sink and serves artifacts.
    sink_secret: SecretStr | None = None
    verifier_form_sinks: str = "default"
    evidence_dir: str = str(Path(__file__).resolve().parents[2] / ".evidence")

    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, v: str) -> str:
        # Render/Neon hand out postgres:// or postgresql:// URLs; we use psycopg 3.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v

    @model_validator(mode="after")
    def _sandbox_only(self) -> Settings:
        if self.paypal_base_url.rstrip("/") != PAYPAL_SANDBOX_BASE_URL:
            raise ValueError(
                "OutcomeGuard only talks to the PayPal Sandbox; "
                f"PAYPAL_BASE_URL must be {PAYPAL_SANDBOX_BASE_URL}"
            )
        return self

    @property
    def paypal_configured(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)

    def public_summary(self) -> dict[str, object]:
        return {
            "app_env": self.app_env,
            "payment_mode": self.payment_mode.value,
            # Rehearsal never touches PayPal; never label it as Sandbox.
            "paypal_environment": (
                "sandbox" if self.payment_mode is PaymentMode.SANDBOX else "none (simulated)"
            ),
            "paypal_credentials_configured": self.paypal_configured,
            "paypal_webhook_configured": bool(self.paypal_webhook_id),
            "sandbox_proof_enabled": self.enable_sandbox_proof,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
