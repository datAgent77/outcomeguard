"""Verifier-only settings. Deliberately does NOT declare any PayPal field, so even when the
worker reads the shared .env file, PayPal credentials are never loaded into its memory."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parents[3]

_PAYPAL_ENV = re.compile(r"^PAYPAL_")


class VerifierSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://localhost/outcomeguard"
    sink_secret: SecretStr | None = None
    sink_base_url: str = "http://127.0.0.1:8010"  # where the API's /sinks/* is reachable
    verifier_form_sinks: str = "default"
    evidence_dir: str = str(API_DIR / ".evidence")
    lighthouse_bin: str = str(API_DIR / "verifier-tools" / "node_modules" / ".bin" / "lighthouse")
    lighthouse_band: int = 5
    verifier_proxy_url: str | None = None  # container mode: external egress proxy
    verifier_runner_url: str | None = None  # container mode: isolated runner job server
    verifier_dev_allow: str = ""
    runner_timeout_seconds: int = 420
    lease_seconds: int = 600

    @field_validator("database_url")
    @classmethod
    def _psycopg(cls, v: str) -> str:
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v

    @property
    def sink_refs(self) -> set[str]:
        return {s.strip() for s in self.verifier_form_sinks.split(",") if s.strip()}


def assert_no_paypal_credentials() -> None:
    leaked = sorted(k for k in os.environ if _PAYPAL_ENV.match(k))
    if leaked:
        raise SystemExit(
            f"verifier refuses to start with PayPal variables in its environment: {leaked}"
        )


@lru_cache
def get_verifier_settings() -> VerifierSettings:
    return VerifierSettings()
