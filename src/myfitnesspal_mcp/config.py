import hashlib
import os
import subprocess
from datetime import timedelta
from pathlib import Path

import platformdirs

APP_NAME = "myfitnesspal-mcp"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_SYNC_DAYS = 30
MAX_SYNC_DAYS = 3650
DEFAULT_TODAY_TTL_SECONDS = 0
DEFAULT_YESTERDAY_TTL_HOURS = 24.0
DEFAULT_RECENT_TTL_HOURS = 24.0
DEFAULT_MUTABLE_HISTORY_DAYS = 30
DEFAULT_RATE_LIMIT_REQUESTS_PER_MINUTE = 6.0
DEFAULT_RATE_LIMIT_BURST = 1
DEFAULT_RATE_LIMIT_JITTER_SECONDS = 2.0
DEFAULT_RETRY_ATTEMPTS = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 2.0
DEFAULT_LOG_LEVEL = "WARNING"


def _restrict_owned_directory(path: Path) -> None:
    if os.name == "nt":
        identity = subprocess.run(
            ["whoami"], capture_output=True, text=True, check=False
        )
        principal = identity.stdout.strip()
        if identity.returncode or not principal:
            raise RuntimeError("could not secure application directory")
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:(OI)(CI)F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode:
            raise RuntimeError("could not secure application directory")
    else:
        path.chmod(0o700)


def _directory(path: Path, *, owned: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if owned:
        _restrict_owned_directory(path)
    return path


def config_dir() -> Path:
    return _directory(Path(platformdirs.user_config_dir(APP_NAME)), owned=True)


def data_dir() -> Path:
    override = os.environ.get("MFP_MCP_DATA_DIR")
    if override:
        path = Path(override)
    else:
        path = Path(platformdirs.user_data_dir(APP_NAME))
    return _directory(path, owned=not bool(override))


def cookies_path() -> Path:
    return config_dir() / "cookies.json"


def normalize_username(username: str) -> str:
    normalized = username.strip().casefold()
    if not normalized:
        raise ValueError("username must not be empty")
    return normalized


def account_key(username: str) -> str:
    return hashlib.sha256(normalize_username(username).encode("utf-8")).hexdigest()


def account_data_dir(username: str) -> Path:
    accounts = _directory(data_dir() / "accounts", owned=True)
    return _directory(accounts / account_key(username), owned=True)


def owned_directory(path: Path) -> Path:
    """Create a private directory beneath an application-owned directory."""
    return _directory(path, owned=True)


def legacy_database_path() -> Path:
    return data_dir() / "data.db"


def database_path(username: str | None = None) -> Path:
    explicit = os.environ.get("MFP_DATABASE_PATH")
    if explicit:
        path = Path(explicit)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return legacy_database_path() if username is None else account_data_dir(username) / "data.db"


def cookie_env() -> str | None:
    return os.environ.get("MFP_COOKIE")


def username_env() -> str | None:
    value = os.environ.get("MFP_USERNAME")
    return value.strip() if value and value.strip() else None


def impersonate() -> str:
    return os.environ.get("MFP_IMPERSONATE", "chrome")


def request_timeout() -> float:
    raw = os.environ.get("MFP_REQUEST_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("MFP_REQUEST_TIMEOUT must be a number") from exc
    if not 0 < value <= 300:
        raise ValueError("MFP_REQUEST_TIMEOUT must be greater than 0 and at most 300")
    return value


def sync_days() -> int:
    raw = os.environ.get("MFP_SYNC_DAYS", str(DEFAULT_SYNC_DAYS))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("MFP_SYNC_DAYS must be an integer") from exc
    if not 1 <= value <= MAX_SYNC_DAYS:
        raise ValueError(f"MFP_SYNC_DAYS must be between 1 and {MAX_SYNC_DAYS}")
    return value


def _non_negative_float(name: str, default: float, *, maximum: float = 86400.0) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} must be between 0 and {maximum}")
    return value


def _positive_int(name: str, default: int, *, maximum: int = MAX_SYNC_DAYS) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


def today_ttl() -> timedelta:
    return timedelta(seconds=_non_negative_float(
        "MFP_TODAY_TTL_SECONDS", DEFAULT_TODAY_TTL_SECONDS
    ))


def yesterday_ttl() -> timedelta:
    return timedelta(hours=_non_negative_float(
        "MFP_YESTERDAY_TTL_HOURS", DEFAULT_YESTERDAY_TTL_HOURS, maximum=8760.0
    ))


def recent_ttl() -> timedelta:
    return timedelta(hours=_non_negative_float(
        "MFP_RECENT_TTL_HOURS", DEFAULT_RECENT_TTL_HOURS, maximum=8760.0
    ))


def mutable_history_days() -> int:
    return _positive_int(
        "MFP_MUTABLE_HISTORY_DAYS", DEFAULT_MUTABLE_HISTORY_DAYS
    )


def rate_limit_requests_per_minute() -> float:
    return _non_negative_float(
        "MFP_RATE_LIMIT_REQUESTS_PER_MINUTE",
        DEFAULT_RATE_LIMIT_REQUESTS_PER_MINUTE,
        maximum=600.0,
    )


def rate_limit_burst() -> int:
    return _positive_int("MFP_RATE_LIMIT_BURST", DEFAULT_RATE_LIMIT_BURST, maximum=20)


def rate_limit_jitter_seconds() -> float:
    return _non_negative_float(
        "MFP_RATE_LIMIT_JITTER_SECONDS", DEFAULT_RATE_LIMIT_JITTER_SECONDS,
        maximum=60.0,
    )


def retry_attempts() -> int:
    return _positive_int("MFP_RETRY_ATTEMPTS", DEFAULT_RETRY_ATTEMPTS, maximum=10)


def retry_backoff_seconds() -> float:
    return _non_negative_float(
        "MFP_RETRY_BACKOFF_SECONDS", DEFAULT_RETRY_BACKOFF_SECONDS, maximum=300.0
    )


def log_level() -> str:
    value = os.environ.get("MFP_LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
    if value not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        raise ValueError("MFP_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    return value
