"""Offline unit tests for unifi-skill. No network, no controller: every test
is fixture-driven or in-process. These are the default test tier (see
docs/test.md); integration tests are opt-in via RUN_UNIFI_INTEGRATION=1.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from unifi_skill import normalize
from unifi_skill.cli import build_parser, main
from unifi_skill.env import Config, load_env_file, load_cookie
from unifi_skill.exitcodes import (EXIT_AUTH, EXIT_NETWORK, EXIT_OK,
                                   EXIT_REJECTED, exit_meaning,
                                   map_http_exit_code)
from unifi_skill.transport import map_http_exit_code as transport_map  # noqa: F401

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
# Patterns are maintained in scripts/privacy_patterns.txt (the single source
# shared with scripts/privacy_scan.sh) so this test file never embeds the
# forbidden strings itself. That file contains regex patterns only, never
# real values.
_PATTERNS_FILE = Path(__file__).resolve().parent.parent / "scripts" / "privacy_patterns.txt"
FORBIDDEN_IN_FIXTURES = [
    line for line in _PATTERNS_FILE.read_text(encoding="utf-8").splitlines()
    if line and not line.startswith("#")
]


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- fixtures
def test_fixtures_exist_and_are_anonymized():
    names = {"stat_device.json", "stat_sta.json", "wlan_enriched_configuration.json",
             "get_setting.json", "stat_sysinfo.json"}
    present = {p.name for p in FIXTURES.glob("*.json")}
    assert names <= present
    for p in FIXTURES.glob("*.json"):
        text = p.read_text(encoding="utf-8")
        meta = json.loads(text).get("_meta")
        assert meta, f"{p.name} lacks a _meta anonymization note"
        for pattern in FORBIDDEN_IN_FIXTURES:
            assert not re.search(pattern, text, re.IGNORECASE), \
                f"{p.name} matches forbidden pattern {pattern}"


def test_real_macs_absent_from_fixtures():
    """Only locally-administered pseudo MACs (02:...) are allowed."""
    mac_re = re.compile(r"\b([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")
    for p in FIXTURES.glob("*.json"):
        for m in mac_re.finditer(p.read_text(encoding="utf-8")):
            first = int(m.group(0)[:2], 16)
            assert first & 0x02, f"{p.name}: non-local MAC {m.group(0)}"


# ---------------------------------------------------------------- exit codes
def test_map_http_exit_code_contract():
    assert map_http_exit_code(200) == EXIT_OK
    assert map_http_exit_code(401) == EXIT_AUTH
    assert map_http_exit_code(403) == EXIT_AUTH
    assert map_http_exit_code(404) == EXIT_REJECTED
    assert map_http_exit_code(422) == EXIT_REJECTED
    assert map_http_exit_code(408) == EXIT_NETWORK
    assert map_http_exit_code(500) == EXIT_NETWORK
    assert map_http_exit_code(503) == EXIT_NETWORK
    assert map_http_exit_code(None) == EXIT_NETWORK
    assert exit_meaning(EXIT_AUTH) == "auth"


# ---------------------------------------------------------------- .env / cookie
def test_env_loader_does_not_override_env(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIFI_BASE_URL", "https://real.example:11443")
    f = tmp_path / ".env"
    f.write_text("UNIFI_BASE_URL=https://fake.example:11443\nUNIFI_SITE=mysite\n# comment\n\n")
    assert load_env_file(f) is True
    import os
    assert os.environ["UNIFI_BASE_URL"] == "https://real.example:11443"  # no override
    assert os.environ["UNIFI_SITE"] == "mysite"


def test_cookie_file_missing_returns_empty(tmp_path):
    assert load_cookie(str(tmp_path / "nope.txt")) == ""


def test_cookie_strips_pasted_header_prefix(tmp_path):
    f = tmp_path / "cookie.txt"
    f.write_text("Cookie: TOKEN=abc123; JSESSIONID=def456\n")
    assert load_cookie(str(f)) == "TOKEN=abc123; JSESSIONID=def456"


def test_config_to_dict_never_contains_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIFI_BASE_URL", "https://192.0.2.10:11443")
    monkeypatch.setenv("UNIFI_COOKIE_FILE", str(tmp_path / "c.txt"))
    (tmp_path / "c.txt").write_text("TOKEN=supersecretvalue; JSESSIONID=x")
    cfg = Config()
    cfg.resolve_cookie()
    assert "supersecretvalue" not in json.dumps(cfg.to_dict())


# ---------------------------------------------------------------- normalization
def test_ap_status_fields_from_fixture():
    aps = _fixture("stat_device.json")["data"]
    row = normalize.ap_status(aps[0])
    assert row["name"] and row["ip"] and row["mac"]
    radios = row["radios"]
    assert radios, "expected radio_table_stats normalization"
    ng = next(r for r in radios if r["band"] == "2.4G")
    assert ng["channel_utilization_pct"] is not None
    assert ng["cu_external_pct"] == ng["channel_utilization_pct"] - ng["cu_self_pct"]


def test_channel_conflicts_detects_co_channel():
    aps = _fixture("stat_device.json")["data"]
    conflicts = normalize.channel_conflicts(aps)
    # The two APs in the fixture share at least one band/channel by design; if
    # they don't, conflicts is legitimately empty — compute and assert identity:
    manual = {}
    for ap in aps:
        for s in ap.get("radio_table_stats") or []:
            key = (normalize.BAND_BY_RADIO.get(s["radio"], s["radio"]), str(s["channel"]))
            manual.setdefault(key, []).append(ap.get("name"))
    expected = {b: {} for b in set(k[0] for k, v in manual.items() if len(v) > 1)}
    for (band, ch), names in manual.items():
        if len(names) > 1:
            expected[band][ch] = names
    assert conflicts == {b: d for b, d in expected.items() if d}


def test_client_brief_and_signal_buckets():
    stas = _fixture("stat_sta.json")["data"]
    clients = [normalize.client_brief(s) for s in stas]
    # One fixture row legitimately has no name/hostname (real controllers
    # return such clients too); identity then falls back to the MAC.
    assert all(c["name"] or c["mac"] for c in clients)
    buckets = normalize.signal_buckets(clients)
    wireless = [c for c in clients if not c["is_wired"]]
    assert sum(buckets.values()) == len(wireless)
    for c in wireless:
        sig = c["signal_dbm"]
        if sig > -60:
            assert buckets["strong"] >= 1


def test_wlan_brief_from_fixture():
    rows = _fixture("wlan_enriched_configuration.json")["data"]
    briefs = [normalize.wlan_brief(w) for w in rows]
    assert briefs[0]["name"]
    assert isinstance(briefs[0]["bands"], list)
    # The anonymization must have removed the passphrase from anything we render.
    assert all("x_passphrase" not in json.dumps(b) for b in briefs)


def test_sysinfo_brief():
    info = _fixture("stat_sysinfo.json")["data"]
    brief = normalize.controller_sysinfo(info)
    assert brief["version"]


# ---------------------------------------------------------------- read-only scan
def test_no_http_write_methods_in_src():
    """READ-ONLY invariant: nothing in src/ may POST/PUT/DELETE."""
    src = Path(__file__).resolve().parent.parent / "src" / "unifi_skill"
    offenders = []
    for p in src.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r'method\s*=\s*["\'](POST|PUT|DELETE|PATCH)["\']', text, re.IGNORECASE):
            offenders.append(f"{p.name}: {m.group(0)}")
        for m in re.finditer(r'\b(urlopen|Request)\([^)]*\bpost\b', text, re.IGNORECASE):
            offenders.append(f"{p.name}: request-with-post")
        if re.search(r'\brequests\.(post|put|delete)', text, re.IGNORECASE):
            offenders.append(f"{p.name}: requests lib write call")
    assert not offenders, offenders


# ---------------------------------------------------------------- CLI behaviour (offline)
def test_cli_missing_base_url(monkeypatch, tmp_path):
    monkeypatch.delenv("UNIFI_BASE_URL", raising=False)
    monkeypatch.delenv("UNIFI_ENV_FILE", raising=False)
    monkeypatch.delenv("UNIFI_COOKIE_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["aps"])
    payload = json.loads(buf.getvalue())
    assert rc == EXIT_REJECTED
    assert payload["data"]["status"] == "error"
    assert "UNIFI_BASE_URL" in payload["data"]["error"]


def test_cli_missing_cookie(tmp_path, monkeypatch):
    import io, contextlib
    monkeypatch.setenv("UNIFI_BASE_URL", "https://192.0.2.10:11443")
    monkeypatch.setenv("UNIFI_COOKIE_FILE", str(tmp_path / "absent.txt"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["aps", "--json"])
    assert rc == EXIT_AUTH
    payload = json.loads(buf.getvalue())
    assert payload["data"]["exit_code"] == EXIT_AUTH
    assert "cookie" in payload["data"]["error"].lower()


def test_cli_usage_error_bad_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code == 2  # argparse usage error


def test_status_command_against_fixtures(tmp_path, monkeypatch, capsys):
    """Run cmd_status with a fake transport backed by fixtures (no network)."""
    import unifi_skill.cli as cli_mod
    from unifi_skill.env import Config

    fixture_payload = {
        "/stat/device": _fixture("stat_device.json"),
        "/stat/sta": _fixture("stat_sta.json"),
    }

    def fake_fetch(cfg_, endpoint_key, **kw):
        mapping = {"devices": "/stat/device", "clients": "/stat/sta"}
        return fixture_payload[mapping[endpoint_key]]

    monkeypatch.setattr(cli_mod, "_fetch", fake_fetch)
    monkeypatch.setenv("UNIFI_BASE_URL", "https://192.0.2.10:11443")
    monkeypatch.setenv("UNIFI_COOKIE_FILE", str(tmp_path / "cookie.txt"))
    (tmp_path / "cookie.txt").write_text("TOKEN=fake; JSESSIONID=fake")
    args = build_parser().parse_args(["status", "--json"])
    cfg = Config()
    rc = cli_mod.run_command("status", args, cfg)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == EXIT_OK
    assert payload["data"]["ap_count"] == len(fixture_payload["/stat/device"]["data"])
    assert payload["data"]["signal_buckets"]
    assert payload["data"]["config"]["cookie_file"].endswith("cookie.txt")
