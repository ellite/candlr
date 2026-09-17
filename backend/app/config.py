import sys
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings

_INSECURE_DEFAULTS = {"change-me-in-production", "change-me-generate-a-random-string", ""}


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/candlr.db"
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    cookie_secure: bool = False  # set True when serving over HTTPS
    docs_enabled: bool = False
    # Used to build absolute links in outgoing emails (e.g. the password
    # reset link). Set this to your real external URL in production.
    server_url: str = "http://localhost:4258"

    # Registration is disabled by default. Set to true to allow additional
    # users; the very first account can always be created regardless of this
    # setting, since a fresh instance would otherwise have no way to bootstrap.
    enable_registrations: bool = False
    registration_max_allowed_users: int = 0  # 0 = unlimited

    # OIDC / SSO
    oidc_enabled: bool = False
    oidc_provider_name: str = "SSO"
    oidc_client_id: Optional[str] = None
    oidc_client_secret: Optional[str] = None
    oidc_auth_url: Optional[str] = None
    oidc_token_url: Optional[str] = None
    oidc_userinfo_url: Optional[str] = None
    # OIDC_REDIRECT_URL must point at this app's /oidc-callback page
    oidc_redirect_url: str = "http://localhost:4258/oidc-callback"
    oidc_identifier_field: str = "email"
    oidc_scopes: str = "openid email profile"
    oidc_auto_create_users: bool = True
    oidc_disable_password_login: bool = False

    # Email notifications - instance-wide SMTP relay; each user opts in/out
    # individually and it always sends to their own account email.
    smtp_address: Optional[str] = None
    smtp_port: int = 587
    smtp_encryption: str = "tls"  # "tls", "ssl", or "none"
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    from_email: Optional[str] = None

    # Web push notifications - instance-wide VAPID identity, shared by every
    # user's subscribed devices. Generate with backend/scripts/generate_vapid_keys.py.
    vapid_public_key: Optional[str] = None
    vapid_private_key: Optional[str] = None
    vapid_subject: str = "mailto:admin@example.com"

    # File storage root - override with DATA_DIR in production if needed.
    data_dir: Path = Path(__file__).parent.parent / "data"

    # IANA timezone name used to compute "today" (dashboard grouping, days-until
    # math, and the reminder scheduler). Defaults to UTC.
    timezone: str = "UTC"

    # Resolved relative to this file rather than the process's CWD, so it
    # finds the repo-root .env regardless of whether the backend is started
    # from backend/ (the documented dev workflow) or elsewhere.
    model_config = {"env_file": Path(__file__).parent.parent.parent / ".env"}

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"


settings = Settings()

if settings.secret_key in _INSECURE_DEFAULTS:
    print(
        "FATAL: SECRET_KEY is set to the default insecure value. "
        "Set a random SECRET_KEY environment variable before starting.",
        file=sys.stderr,
    )
    sys.exit(1)
