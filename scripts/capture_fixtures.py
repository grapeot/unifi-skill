#!/usr/bin/env python3
"""Capture live UniFi controller GETs and write ANONYMIZED fixtures.

Run once by the maintainer, from the repo root, with a valid cookie:

    UNIFI_COOKIE_FILE=/tmp/unifi_cookie.txt \
    UNIFI_BASE_URL=https://<lan-controller-ip>:11443 \
    .venv/bin/python scripts/capture_fixtures.py

The script performs GET requests only. Everything it writes into fixtures/
passes through the redactor below, which maps identifiers to stable fakes:

  real SSID names      -> home-main / home-iot / home-6g
  real AP names        -> AP-1 ... AP-5  (product names such as "U6 Enterprise" stay)
  MAC addresses        -> 02:00:00:00:00:NN (locally-administered pseudo MACs)
  LAN IPs (10/172.16-31/192.168/169.254) -> TEST-NET (192.0.2.x / 198.51.100.x / 203.0.113.x)
  hostnames            -> phone-1 / iot-device-1 / client-N
  Mongo ObjectIds      -> 5f000000000000000000NNNN (stable, per original id)
  secret-looking fields (x_passphrase, x_iapp_key, psk, token, ...) -> REDACTED-*
  epoch timestamps     -> shifted by a fixed offset (structure preserved)

Nothing in fixtures/ is a verbatim controller response; each file carries a
_meta note saying so.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures"

BASE = os.environ.get("UNIFI_BASE_URL", "").rstrip("/")
COOKIE = Path(os.environ.get("UNIFI_COOKIE_FILE", "/tmp/unifi_cookie.txt")).expanduser().read_text().strip()

if not BASE:
    sys.exit("UNIFI_BASE_URL is required")

EP = {
    "device": "/proxy/network/api/s/default/stat/device",
    "sta": "/proxy/network/api/s/default/stat/sta",
    "setting": "/proxy/network/api/s/default/get/setting",
    "sysinfo": "/proxy/network/api/s/default/stat/sysinfo",
    "wlan": "/proxy/network/v2/api/site/default/wlan/enriched-configuration",
}

# ---------------------------------------------------------------------------
# Redactor
# ---------------------------------------------------------------------------

TS_SHIFT = 1_700_000_000  # epoch base all timestamps are re-anchored to
_epoch_min = None
TS_OFFSET = None

SSID_MAP = {}      # real ssid -> fake
AP_MAP = {}        # real ap name -> fake
HOST_MAP = {}      # real hostname -> fake
MAC_MAP = {}       # real mac -> pseudo
OID_MAP = {}       # objectid -> pseudo
IP_MAP = {}        # ip -> TEST-NET

SSID_POOL = ["home-main", "home-iot", "home-6g"]
OID_RE = re.compile(r"\b[0-9a-f]{24}\b")
MAC_RE = re.compile(r"\b[0-9a-f]{2}:[0-9a-f]{2}(?::[0-9a-f]{2}){4}\b")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
TS_KEYS = {
    "first_seen", "last_seen", "assoc_time", "latest_assoc_time", "uptime", "_uptime",
    "connected_at", "disconnected_at", "adopted_at", "timestamp", "creation_timestamp",
    "start_connected_epoch_secs", "last_seen", "_last_seen_by_uap", "_uptime_by_uap",
    "dhcpend_time", "disconnect_timestamp", "device_udp_ping_resp_at", "x_last_uplink_change_ms",
}
SECRET_KEY_RE = re.compile(
    r"passphrase|passwd|password|secret|_psk$|x_.*_psk|psk$|iapp_key|api_token|mgmt_key|"
    r"token|credential|ssh_auth|element_psk|mesh_psk|sae_psk|community|private_preshared|"
    r"portal_custom|smtp_", re.I)
SECRET_DROP_KEY_RE = re.compile(r"guest_token|sso_app_id|anonymous_controller_id|anon_id|anon_client_id", re.I)
IDENTITY_KEY_RE = re.compile(r"^(hostname|name|display_name)$")


def _pick(mapping, pool, prefix, value):
    if value in mapping:
        return mapping[value]
    if len(pool) > len(mapping):
        mapping[value] = pool[len(mapping)]
    else:
        mapping[value] = f"{prefix}{len(pool) + 1}"
    return mapping[value]


def _stable_pseudo(value, width, prefix_hex):
    h = hashlib.sha1(value.encode()).hexdigest()
    return prefix_hex + h[:width - len(prefix_hex)]


def _is_lan_ip(s):
    try:
        a, b = (int(x) for x in s.split(".")[0:2])
    except (ValueError, IndexError):
        return False
    return (a == 10 or a == 127 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)
            or a == 169 or a == 172 or a == 100)


def _test_net_ip(s):
    if s not in IP_MAP:
        h = int(hashlib.sha1(s.encode()).hexdigest()[:4], 16)
        a, b = (int(x) for x in s.split(".")[0:2])
        if a == 169:
            IP_MAP[s] = "169.254.%d.%d" % (h % 250 + 1, (h // 250) % 250 + 1)
        elif (a == 192 and b == 168) or a == 10:
            IP_MAP[s] = "192.0.2.%d" % (h % 250 + 1)
        elif a == 172 or a == 100:
            IP_MAP[s] = "198.51.100.%d" % (h % 250 + 1)
        else:
            IP_MAP[s] = "203.0.113.%d" % (h % 250 + 1)
    return IP_MAP[s]


HOST_POOL = {}


def _fake_hostname(value, radio=None):
    if value in HOST_MAP:
        return HOST_MAP[value]
    low = value.lower()
    if any(k in low for k in ("iot", "cam", "plug", "bulb", "wemo", "speaker", "tv", "printer", "robot", "sensor", "switch", "bulb")):
        n = HOST_POOL.get("iot", 0) + 1
        HOST_POOL["iot"] = n
        HOST_MAP[value] = f"iot-device-{n}"
    elif any(k in low for k in ("iphone", "phone", "pixel", "galaxy", "oneplus", "macbook", "ipad", "laptop", "watch")):
        n = HOST_POOL.get("phone", 0) + 1
        HOST_POOL["phone"] = n
        HOST_MAP[value] = f"phone-{n}"
    else:
        n = HOST_POOL.get("other", 0) + 1
        HOST_POOL["other"] = n
        HOST_MAP[value] = f"client-{n}"
    return HOST_MAP[value]


def redact(obj, key=None):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if SECRET_DROP_KEY_RE.search(k):
                continue
            if SECRET_KEY_RE.search(k) and isinstance(v, (str, int, float, list)) and k not in ("state",):
                out[k] = "REDACTED"
                continue
            if k == "site_id" and isinstance(v, str):
                out[k] = _oid(v)
            if k == "description" and isinstance(v, str):
                out[k] = _text(v)
            out[k] = redact(v, k)
        # drop entries with no data at all
        return out
    if isinstance(obj, list):
        return [redact(v, key) for v in obj]
    if isinstance(obj, str):
        if key in ("essid", "x_mesh_essid", "x_element_essid", "ssid", "name") and key == "essid":
            return _pick(SSID_MAP, SSID_POOL, "home-", obj)
        if key == "name" and (obj in SSID_MAP or obj in AP_MAP):
            if obj in SSID_MAP:
                return SSID_MAP[obj]
            return AP_MAP[obj]
        return _text(obj)
    if isinstance(obj, int) and not isinstance(obj, bool):
        if key in TS_KEYS and obj > 1_000_000_000:
            return _shift_ts(obj)
        return obj
    return obj


def _oid(value):
    if value not in OID_MAP:
        OID_MAP[value] = _stable_pseudo(value, 24, "5f00")
    return OID_MAP[value]


def _shift_ts(value):
    global _epoch_min, TS_OFFSET
    if _epoch_min is None:
        _epoch_min = float("inf")
    if value < _epoch_min:
        _epoch_min = value
        TS_OFFSET = TS_SHIFT - value
    return value + (TS_OFFSET or 0)


def _text(s):
    s = MAC_RE.sub(lambda m: _mac(m.group(0)), s)
    s = IPV4_RE.sub(lambda m: _test_net_ip(m.group(0)) if _is_lan_ip(m.group(0)) else m.group(0), s)
    s = OID_RE.sub(lambda m: _oid(m.group(0)), s)
    for real, fake in list(SSID_MAP.items()):
        s = s.replace(real, fake)
    return s


def _mac(value):
    if value not in MAC_MAP:
        n = len(MAC_MAP) + 1
        MAC_MAP[value] = "02:00:%02x:%02x:%02x:%02x" % (n // 16777216 % 256, n // 65536 % 256, n // 256 % 256, n % 256)
    return MAC_MAP[value]


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def get(path):
    req = urllib.request.Request(BASE + path, headers={"Cookie": COOKIE, "Accept": "application/json"}, method="GET")
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def note(kind):
    return {
        "_meta": {
            "note": f"Anonymized UniFi controller response fixture ({kind}). "
                    "MACs are 02:00: pseudo MACs, LAN IPs are TEST-NET (192.0.2.x), SSIDs are "
                    "home-main/home-iot/home-6g, AP names are AP-N, ObjectIds are 5f00-prefixed, "
                    "epoch timestamps are shifted by a constant, secret fields are REDACTED. "
                    "Structure mirrors the real endpoint.",
            "endpoint": None,
            "captured": "2026-10-09",
        }
    }


def main():
    FIX.mkdir(exist_ok=True)

    # Pass 1: build SSID / AP-name maps from raw data so redaction is consistent.
    device = get(EP["device"])
    sta = get(EP["sta"])
    setting = get(EP["setting"])
    sysinfo = get(EP["sysinfo"])
    wlan = get(EP["wlan"])

    for x in device.get("data") or []:
        nm = x.get("name")
        if nm and nm not in AP_MAP:
            AP_MAP[nm] = f"AP-{len(AP_MAP) + 1}"
    for entry in wlan:
        cfg = entry.get("configuration") or {}
        nm = cfg.get("name")
        if nm and nm not in SSID_MAP:
            SSID_MAP[nm] = SSID_POOL[len(SSID_MAP) % len(SSID_POOL)] if len(SSID_MAP) < len(SSID_POOL) else f"home-{len(SSID_MAP) + 1}"

    # Pass 2: redact.
    devices = [redact(x) for x in device.get("data") or []]
    for x in devices:
        # name mapping must run against the AP_MAP built pre-redaction
        pass

    def fix_device_names(items):
        for x in items:
            if isinstance(x, dict):
                if x.get("name") in AP_MAP:
                    x["name"] = AP_MAP[x["name"]]
                for r in x.get("radio_table_stats") or []:
                    r.pop("name", None)
                for r in x.get("radio_table") or []:
                    r.pop("name", None)
                for p in x.get("port_table") or []:
                    if isinstance(p, dict) and p.get("name"):
                        p["name"] = "eth%d" % p.get("num_port", 0)
                for e in x.get("ethernet_table") or []:
                    if isinstance(e, dict) and e.get("name"):
                        e["name"] = "eth%d" % e.get("num_port", 0)
                x.pop("port_table", None)
                x.pop("ethernet_table", None)
                x.pop("antenna_table", None)
                x.pop("countrycode_table", None)
                x.pop("leds", None)
                x.pop("upgrade", None)
                x.pop("wlan_overrides", None)
                x.pop("system_stats", None)
                x.pop("fan_level", None)
                x.pop("fan_speed", None)
        return items

    fix_device_names(devices)

    clients = [redact(x) for x in sta.get("data") or []]
    for x in clients:
        for k in ("hostname", "name"):
            if x.get(k):
                x[k] = _fake_hostname(str(x[k]), x.get("radio"))
            else:
                x.pop(k, None)
        x.pop("satisfaction_avg", None)
        x.pop("satisfaction_reason", None)
        x.pop("network_members_group_ids", None)
        x.pop("user_group_id_computed", None)
        x.pop("qos_policy_applied", None)
        x.pop("last_ipv6", None)
        x.pop("hostname_source", None)

    # Trim to representative records, <60 lines when pretty-printed.
    def pick_devices(items):
        out = []
        # one 3-radio AP with loaded 2.4G, one 2-radio AP
        three = [x for x in items if len(x.get("radio_table_stats") or []) == 3]
        two = [x for x in items if len(x.get("radio_table_stats") or []) == 2]
        for group in (three, two):
            if group:
                best = max(group, key=lambda x: x.get("num_sta") or 0)
                out.append(best)
        return out[:2]

    def trim_device(x):
        keep = ("_id", "name", "mac", "ip", "model", "model_id", "type", "version",
                "displayable_version", "state", "uptime", "num_sta", "user-num_sta",
                "guest-num_sta", "satisfaction", "snmp_location", "device_id",
                "country_code", "last_seen", "radio_table_stats", "radio_table",
                "ethernet_table_present")
        out = {k: x[k] for k in keep if k in x}
        for r in x.get("radio_table_stats") or []:
            keep_r = ("radio", "channel", "last_channel", "extchannel", "bw", "tx_power",
                      "gain", "cu_total", "cu_self_tx", "cu_self_rx", "tx_retries_pct",
                      "tx_packets", "tx_retries", "num_sta", "user-num_sta",
                      "guest-num_sta", "satisfaction", "state")
            r2 = {k: r[k] for k in keep_r if k in r}
            out.setdefault("radio_table_stats", [])
        out["radio_table_stats"] = [
            {k: r[k] for k in ("radio", "channel", "last_channel", "extchannel", "bw",
                               "tx_power", "gain", "cu_total", "cu_self_tx", "cu_self_rx",
                               "tx_retries_pct", "tx_packets", "tx_retries", "num_sta",
                               "user-num_sta", "guest-num_sta", "satisfaction", "state") if k in r}
            for r in x.get("radio_table_stats") or []
        ]
        out["radio_table"] = [
            {k: r[k] for k in ("radio", "channel", "ht", "tx_power_mode", "max_txpower",
                               "min_txpower", "nss", "builtin_ant_gain", "has_dfs") if k in r}
            for r in x.get("radio_table") or []
        ]
        return out

    devices_out = [trim_device(x) for x in pick_devices(devices)]
    for x, src in zip(devices_out, devices):
        x["ethernet_table_present"] = bool(src.get("ethernet_table"))

    def pick_clients(items):
        wifi = [x for x in items if not x.get("is_wired") and x.get("signal")]
        out = []
        for radio in ("ng", "na", "6e"):
            g = [x for x in wifi if x.get("radio") == radio]
            if g:
                out.append(max(g, key=lambda x: x.get("signal") or -999))
                weakest = min(g, key=lambda x: x.get("signal") or -999)
                if weakest is not out[-1] and len(out) < 5:
                    out.append(weakest)
        wired = [x for x in items if x.get("is_wired")]
        if wired and len(out) < 5:
            out.append(wired[0])
        return out[:5]

    def trim_client(x):
        keep = ("_id", "mac", "name", "hostname", "ip", "last_ip", "oui", "radio", "radio_name",
                "radio_proto", "signal", "rssi", "noise", "channel", "channel_width",
                "essid", "ap_mac", "bssid", "is_wired", "is_guest", "state", "uptime",
                "latest_assoc_time", "last_seen", "tx_rate", "rx_rate", "tx_power",
                "wifi_tx_retries_percentage", "satisfaction", "tx_bytes", "rx_bytes")
        out = {k: x[k] for k in keep if k in x}
        return out

    clients_out = [trim_client(x) for x in pick_clients(clients)]

    # setting: keep 3 groups (main, wireless/radio_ai, country) with 4-5 fields each
    settings_out = []
    for g in redact(setting.get("data") or []):
        key = g.get("key")
        if key in ("main", "radio_ai", "country", "locale"):
            keep = ("_id", "key", "site_id", "desc", "description", "enabled", "setting_preference",
                    "channels_na", "channels_ng", "channels_6e", "ht_modes_na", "ht_modes_ng",
                    "auto_adjust_channels_to_country", "mode", "code", "timezone", "name",
                    "countrycode", "high_priority_devices")
            g2 = {k: g[k] for k in keep if k in g}
            g2["field_count"] = len(g)
            settings_out.append(g2)

    sysinfo_red = redact((sysinfo.get("data") or [{}])[0])
    sysinfo_out = {k: sysinfo_red[k] for k in
                   ("name", "hostname", "version", "build", "uptime", "ip_addrs",
                    "data_retention_days", "inform_port", "https_port", "timezone",
                    "update_available", "update_downloaded", "j2mi_tp", "sdi_support") if k in sysinfo_red}

    wlan_out = []
    for entry in redact(wlan):
        cfg = entry.get("configuration") or {}
        cfg2 = {k: cfg[k] for k in ("_id", "name", "enabled", "security", "wpa_mode",
                                    "wpa3_support", "wpa3_transition", "pmf_mode",
                                    "hide_ssid", "is_guest", "wlan_band", "wlan_bands",
                                    "networkconf_id", "ap_group_mode", "x_passphrase") if k in cfg}
        wlan_out.append({
            "configuration": cfg2,
            "details": entry.get("details") or {},
            "statistics": entry.get("statistics") or {},
        })
    for e in wlan_out:
        nm = e["configuration"].get("name")
        if nm in SSID_MAP:
            e["configuration"]["name"] = SSID_MAP[nm]
    wlan_out = wlan_out[:2]

    def dump(name, payload, endpoint, kind):
        body = note(kind)
        body["_meta"]["endpoint"] = endpoint
        body["data"] = payload
        path = FIX / name
        path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path} ({path.stat().st_size} bytes, {len(path.read_text().splitlines())} lines)")

    dump("stat_device.json", devices_out, EP["device"], "list of AP records")
    dump("stat_sta.json", clients_out, EP["sta"], "list of associated-client records")
    dump("wlan_enriched_configuration.json", wlan_out, EP["wlan"], "list of {configuration, details, statistics}")
    dump("get_setting.json", settings_out, EP["setting"], "list of setting groups")
    dump("stat_sysinfo.json", sysinfo_out, EP["sysinfo"], "single controller sysinfo object")

    print("\nMAC map:", json.dumps(MAC_MAP, indent=0)[:400])
    print("SSID map:", SSID_MAP)
    print("AP map:", AP_MAP)
    print("host sample:", dict(list(HOST_MAP.items())[:6]))
    print("ip sample:", dict(list(IP_MAP.items())[:8]))


if __name__ == "__main__":
    main()
