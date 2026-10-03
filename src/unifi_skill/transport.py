"""HTTP transport. READ-ONLY by construction: the only request primitive in
this package is ``get_json`` — there is deliberately no POST/PUT/DELETE
function, and a unit test scans this package for write-method literals.

The controller is a LAN device behind a self-signed cert, so TLS verification
is off by default (docs/rfc.md D5); set UNIFI_TLS_VERIFY=1 to require it.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from .exitcodes import EXIT_AUTH, EXIT_NETWORK, EXIT_REJECTED, map_http_exit_code

# Legacy UI API surface (UniFi OS Server proxies it under /proxy/network).
LEGACY_BASE = "/proxy/network/api/s/{site}"
# v2 surface: SSID list only exists under enriched-configuration; a plain
# GET /v2/api/site/{site}/wlan returns 404 on this generation (docs/rfc.md D6).
V2_BASE = "/proxy/network/v2/api/site/{site}"

COOKIE_EXPIRED_HINT = (
    "Cookie missing or expired. Log into the controller UI in a browser and "
    "export the TOKEN + JSESSIONID cookies into the cookie file "
    "(see docs/cookie_refresh.md)."
)


class ControllerError(Exception):
    """A controller-level failure already mapped onto the exit-code contract."""

    def __init__(self, message: str, exit_code: int = EXIT_NETWORK,
                 http_status: Optional[int] = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.http_status = http_status


def _ssl_context(verify: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _looks_like_login_page(body: str) -> bool:
    head = body[:600].lstrip().lower()
    return head.startswith("<!doctype html") or head.startswith("<html")


def get_json(base_url: str, path: str, cookie: str, *, timeout: float = 30.0,
             tls_verify: bool = False, site: str = "default",
             v2: bool = False) -> Tuple[int, Dict[str, Any]]:
    """GET ``path`` (a {site}-templated relative path) and return
    ``(http_status, parsed_json)``. Raises ControllerError on any failure,
    with exit codes per the contract.
    """
    if not base_url:
        raise ControllerError(
            "UNIFI_BASE_URL is not set (see .env.example).", EXIT_REJECTED)
    if not cookie:
        raise ControllerError(COOKIE_EXPIRED_HINT, EXIT_AUTH)

    template = V2_BASE if v2 else LEGACY_BASE
    url = base_url.rstrip("/") + template.format(site=site) + path
    req = urllib.request.Request(url, method="GET")
    req.add_header("Cookie", cookie)
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "unifi-skill/read-only")

    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_ssl_context(tls_verify)) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read().decode("utf-8", "replace")
        except Exception:
            raw = ""
        code = map_http_exit_code(status)
        if status in (401, 403):
            raise ControllerError(f"{COOKIE_EXPIRED_HINT} (HTTP {status})", EXIT_AUTH, status)
        detail = raw[:300].replace("\n", " ") if raw else exc.reason
        raise ControllerError(f"HTTP {status} from {url}: {detail}", code, status)
    except urllib.error.URLError as exc:
        raise ControllerError(f"Cannot reach controller at {url}: {exc.reason}",
                              EXIT_NETWORK)
    except (TimeoutError, ssl.SSLError, ConnectionError, OSError) as exc:
        raise ControllerError(f"Network/TLS failure talking to {url}: {exc}",
                              EXIT_NETWORK)

    if status in (401, 403):
        raise ControllerError(f"{COOKIE_EXPIRED_HINT} (HTTP {status})", EXIT_AUTH, status)
    if _looks_like_login_page(raw):
        # Some controller generations answer expired sessions with a 200 +
        # login page instead of 401. Map it to auth explicitly.
        raise ControllerError(f"{COOKIE_EXPIRED_HINT} (200 with HTML login page)",
                              EXIT_AUTH, status)

    code = map_http_exit_code(status)
    if code != 0:
        raise ControllerError(f"HTTP {status} from {url}: {raw[:300]}",
                              code, status)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ControllerError(f"Non-JSON response from {url}: {exc}",
                              EXIT_REJECTED, status)
    if isinstance(payload, dict) and payload.get("meta", {}).get("rc") not in (None, "ok"):
        rc = payload["meta"].get("rc")
        message = payload["meta"].get("msg") or "controller returned non-ok meta.rc"
        raise ControllerError(f"Controller rejected request ({rc}): {message}",
                              EXIT_REJECTED, status)
    if isinstance(payload, dict) and payload.get("success") is False:
        message = payload.get("error") or payload.get("message") or "success=false"
        raise ControllerError(f"Controller rejected request: {json.dumps(message)[:300]}",
                              EXIT_REJECTED, status)
    return status, payload


def data_of(payload: Dict[str, Any]) -> Any:
    """Legacy endpoints wrap rows in {"data": [...]}; v2 wraps in {"data": ...} too."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload
