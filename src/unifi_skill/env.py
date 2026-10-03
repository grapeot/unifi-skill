"""Environment resolution: minimal stdlib .env loader and configuration.

Loader semantics mirror the firecrawl-skill lineage: parse ``KEY=VALUE`` lines,
skip blanks and ``#`` comments, strip one layer of quotes, and never override a
variable that is already set in the real environment. The first ``.env`` found
wins (explicit ``--env-file`` first, then an ``UNIFI_ENV_FILE`` pointer, then
the working directory and its parents).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Defaults are documented in README.md and docs/rfc.md (D1, D5).
DEFAULT_COOKIE_PATH = "~/.config/unifi-skill/cookie.txt"
DEFAULT_SITE = "default"
DEFAULT_EXPORT_DIRNAME = "tmp/unifi-backups"
DEFAULT_TIMEOUT = 30.0

ENV_FILENAME = "UNIFI_ENV_FILE"


def load_env_file(env_file: Path) -> bool:
    """Load one KEY=VALUE file without overriding the real environment."""
    if not env_file.exists():
        return False
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
    return True


def load_workspace_env(explicit_env_file: str | None = None) -> Path | None:
    """Return the .env file that was applied, or None when none was found."""
    candidates: list[Path] = []
    if explicit_env_file:
        candidates.append(Path(explicit_env_file).expanduser().resolve())
    pointer = os.environ.get(ENV_FILENAME)
    if pointer:
        candidates.append(Path(pointer).expanduser().resolve())

    cwd = Path.cwd()
    candidates.extend(parent / ".env" for parent in [cwd] + list(cwd.parents))

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if load_env_file(candidate):
            return candidate
    return None


def load_cookie(cookie_file: str | None = None) -> str:
    """Read the controller session cookie value (TOKEN + JSESSIONID).

    Returns "" when the file is missing or empty — callers map that to exit
    code 10 and point at docs/cookie_refresh.md. The file content may be a raw
    ``Cookie:`` header line; the leading header name is stripped.
    """
    raw = cookie_file if cookie_file else os.environ.get("UNIFI_COOKIE_FILE", DEFAULT_COOKIE_PATH)
    if not raw:
        return ""
    path = Path(os.path.expanduser(raw))
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    # Accept "Cookie: TOKEN=...; JSESSIONID=..." pasted from DevTools.
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    return text


class Config:
    """Everything the transport and commands need, resolved once in main()."""

    def __init__(self, env_file: str | None = None) -> None:
        self.env_path = load_workspace_env(env_file)
        self.env_file = env_file
        self.base_url = os.environ.get("UNIFI_BASE_URL", "").rstrip("/")
        self.cookie_file = os.environ.get("UNIFI_COOKIE_FILE", DEFAULT_COOKIE_PATH)
        self.cookie = ""
        self.site = os.environ.get("UNIFI_SITE", DEFAULT_SITE) or DEFAULT_SITE
        self.timeout = _float_env("UNIFI_TIMEOUT", DEFAULT_TIMEOUT)
        # D5: self-signed LAN certs — verification is off unless asked for.
        self.tls_verify = os.environ.get("UNIFI_TLS_VERIFY", "").strip() in ("1", "true", "yes", "on")

    def resolve_cookie(self) -> str:
        self.cookie = load_cookie(self.cookie_file)
        return self.cookie

    def export_dir(self) -> Path:
        raw = os.environ.get("UNIFI_EXPORT_DIR")
        if raw:
            return Path(os.path.expanduser(raw)).resolve()
        return (Path.cwd() / DEFAULT_EXPORT_DIRNAME).resolve()

    def to_dict(self) -> dict[str, Any]:
        """Envelope-safe config view: never includes the cookie value."""
        return {
            "base_url": self.base_url or None,
            "site": self.site,
            "cookie_file": self.cookie_file,
            "cookie_loaded": bool(self.cookie),
            "tls_verify": self.tls_verify,
            "timeout": self.timeout,
            "env_file": str(self.env_path) if self.env_path else None,
        }


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default
