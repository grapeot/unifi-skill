"""Opt-in live controller integration tests (docs/test.md).

Skip unless RUN_UNIFI_INTEGRATION=1 and UNIFI_BASE_URL/UNIFI_COOKIE_FILE
point at a reachable controller. Exercises real GETs through the CLI paths.
"""
from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.integration

RUN = os.environ.get("RUN_UNIFI_INTEGRATION") == "1"
BASE = os.environ.get("UNIFI_BASE_URL", "")
COOKIE_FILE = os.environ.get("UNIFI_COOKIE_FILE", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (RUN and BASE and os.path.exists(COOKIE_FILE or "___absent___")),
        reason="set RUN_UNIFI_INTEGRATION=1 with UNIFI_BASE_URL and UNIFI_COOKIE_FILE",
    ),
]


def _run(cli_main, argv):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli_main(argv)
    return rc, buf.getvalue()


@pytest.fixture(scope="module")
def cli_main():
    from unifi_skill.cli import main
    return main


def test_aps_live(cli_main):
    rc, out = _run(cli_main, ["aps", "--json"])
    assert rc == 0, out
    payload = json.loads(out)
    assert payload["data"]["ap_count"] >= 1
    assert payload["data"]["aps"][0]["mac"]


def test_status_live(cli_main):
    rc, out = _run(cli_main, ["status", "--json"])
    assert rc == 0, out
    d = json.loads(out)["data"]
    for ap in d["aps"]:
        for r in ap["radios"]:
            if r["channel_utilization_pct"] is not None and r["cu_self_pct"] is not None:
                ext = r["channel_utilization_pct"] - r["cu_self_pct"]
                assert r["cu_external_pct"] == ext


def test_wlan_live(cli_main):
    rc, out = _run(cli_main, ["wlan", "--json"])
    assert rc == 0, out
    d = json.loads(out)["data"]
    assert d["wlan_count"] >= 1
    # Passphrases must never be projected, live or otherwise.
    assert "x_passphrase" not in out


def test_clients_weak_live(cli_main):
    rc, out = _run(cli_main, ["clients", "--weak", "--json"])
    assert rc == 0, out
    d = json.loads(out)["data"]
    for c in d["clients"]:
        assert c["signal_dbm"] is not None and c["signal_dbm"] <= -70


def test_export_live(cli_main, tmp_path, monkeypatch):
    monkeypatch.setenv("UNIFI_EXPORT_DIR", str(tmp_path))
    rc, out = _run(cli_main, ["export", "--json"])
    assert rc == 0, out
    d = json.loads(out)["data"]
    assert len(d["exported"]) == 5
    for p in d["exported"].values():
        assert os.path.exists(p)
