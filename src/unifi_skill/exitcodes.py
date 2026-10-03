"""Exit-code contract (mirrors the firecrawl-skill lineage; see README.md,
AGENTS.md, skills/skill_unifi.md). Any change here must be reflected in all
three documents and the unit tests together.
"""

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_AUTH = 10  # missing/expired cookie, or upstream 401/403
EXIT_REJECTED = 12  # other 4xx, or upstream success=false with no usable data
EXIT_NETWORK = 13  # timeout / TLS / DNS / connection failure / 408 / 5xx

EXIT_MEANINGS = {
    EXIT_OK: "ok",
    EXIT_USAGE: "usage",
    EXIT_AUTH: "auth",
    EXIT_REJECTED: "rejected-no-data",
    EXIT_NETWORK: "network-server",
}


def exit_meaning(code: int) -> str:
    return EXIT_MEANINGS.get(code, f"exit-{code}")


def map_http_exit_code(status):
    """HTTP status -> exit code. Shared by transport and commands."""
    if status is None:
        return EXIT_NETWORK
    if 200 <= status < 300:
        return EXIT_OK
    if status in (401, 403):
        return EXIT_AUTH
    if status in (408,) or status >= 500:
        return EXIT_NETWORK
    if 400 <= status < 500:
        return EXIT_REJECTED
    return EXIT_NETWORK
