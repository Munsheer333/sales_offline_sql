"""Runtime guards for strict offline execution."""

from urllib.parse import urlparse


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def require_local_http_url(url: str, setting_name: str, offline_mode: bool = True) -> str:
    """Reject remote service URLs when strict offline mode is enabled."""
    value = (url or "").strip().rstrip("/")
    if not value:
        raise RuntimeError(f"{setting_name} must not be empty")

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError(f"{setting_name} must be an HTTP URL")
    if offline_mode and parsed.hostname.lower() not in LOOPBACK_HOSTS:
        raise RuntimeError(
            f"{setting_name} must use a loopback host while OFFLINE_MODE=true; "
            f"received {parsed.hostname!r}"
        )
    return value


def require_offline_embedding_mode(offline_mode: bool, local_files_only: bool) -> None:
    """Prevent accidental Hugging Face downloads in strict offline mode."""
    if offline_mode and not local_files_only:
        raise RuntimeError("EMBED_LOCAL_FILES_ONLY must be true while OFFLINE_MODE=true")
