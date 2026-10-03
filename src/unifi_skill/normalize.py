"""Normalization of raw controller rows into stable, envelope-friendly dicts.

All functions are pure (dict/list in, dict/list out) so tests run offline
against fixtures/. Field-name choices follow the raw controller where the
names are already stable; unstable or verbose blobs are dropped.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

BAND_BY_RADIO = {"ng": "2.4G", "na": "5G", "6e": "6G"}

WEAK_SIGNAL_DBM = -70  # clients at or below this are "weak"
SIGNAL_BUCKETS = [
    # (label, min_inclusive, max_inclusive) with min <= max, walking upward.
    ("very_weak", -300, -81),  # < -80 dBm
    ("weak", -80, -71),         # -80 .. -71
    ("medium", -70, -61),       # -70 .. -61
    ("strong", -60, 0),         # >= -60
]


def ap_brief(ap: Dict[str, Any]) -> Dict[str, Any]:
    """One AP row from stat/device -> inventory entry."""
    return {
        "name": ap.get("name"),
        "model": ap.get("model"),
        "shortname": ap.get("shortname"),
        "ip": ap.get("ip"),
        "mac": ap.get("mac"),
        "snmp_location": ap.get("snmp_location") or None,
        "firmware": ap.get("version"),
        "state": ap.get("state"),
        "uptime_seconds": ap.get("uptime"),
        "clients": ap.get("num_sta"),
        "satisfaction": ap.get("satisfaction"),
    }


def _radio_row(stats: Dict[str, Any]) -> Dict[str, Any]:
    cu_total = stats.get("cu_total")
    self_tx = stats.get("cu_self_tx") or 0
    self_rx = stats.get("cu_self_rx") or 0
    cu_self = (self_tx + self_rx) if cu_total is not None else None
    return {
        "radio": stats.get("radio"),
        "band": BAND_BY_RADIO.get(stats.get("radio"), stats.get("radio")),
        "channel": stats.get("channel"),
        "channel_width_mhz": stats.get("bw"),
        "tx_power_dbm": stats.get("tx_power"),
        "clients": stats.get("num_sta"),
        "channel_utilization_pct": cu_total,
        "cu_self_pct": cu_self,
        # cu_total - cu_self is airtime eaten by neighbours/other EM sources;
        # this split is the core diagnostic (docs/rfc.md D7).
        "cu_external_pct": (cu_total - cu_self) if cu_total is not None else None,
        "retries_pct": stats.get("tx_retries_pct"),
        "satisfaction": stats.get("satisfaction"),
        "state": stats.get("state"),
    }


def _radio_configs(ap: Dict[str, Any]) -> Dict[str, Any]:
    """Desired-radio config (radio_table): channel auto/manual, ht modes."""
    out: Dict[str, Any] = {}
    for row in ap.get("radio_table") or []:
        out[row.get("radio", "?")] = {
            "channel": row.get("channel"),
            "ht": row.get("ht"),
            "tx_power_mode": row.get("tx_power_mode"),
        }
    return out


def ap_status(ap: Dict[str, Any]) -> Dict[str, Any]:
    """Full per-AP RF status row for the `status` command."""
    radios = [_radio_row(s) for s in ap.get("radio_table_stats") or []]
    return {
        **ap_brief(ap),
        "radios": radios,
        "radio_configs": _radio_configs(ap),
    }


def channel_conflicts(aps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Band/channel reuse ACROSS our own APs (same band+channel = co-channel)."""
    by_band: Dict[str, Dict[str, List[str]]] = {}
    for ap in aps:
        for s in ap.get("radio_table_stats") or []:
            ch = s.get("channel")
            if ch in (None, "auto", 0):
                continue
            band = BAND_BY_RADIO.get(s.get("radio"), s.get("radio"))
            by_band.setdefault(band, {}).setdefault(str(ch), []).append(
                ap.get("name") or ap.get("mac"))
    conflicts = {}
    for band, channels in by_band.items():
        dupes = {ch: names for ch, names in channels.items() if len(names) > 1}
        if dupes:
            conflicts[band] = dupes
    return conflicts


def client_brief(sta: Dict[str, Any]) -> Dict[str, Any]:
    name = sta.get("name") or sta.get("hostname")
    brief = {
        "name": name,
        "vendor": sta.get("oui"),
        "mac": sta.get("mac"),
        "ip": sta.get("ip"),
        "band": BAND_BY_RADIO.get(sta.get("radio"), sta.get("radio")),
        "channel": sta.get("channel"),
        "signal_dbm": sta.get("signal"),
        "essid": sta.get("essid"),
        "ap_mac": sta.get("ap_mac"),
        "is_wired": sta.get("is_wired", False),
        "satisfaction": sta.get("satisfaction"),
        "retries_pct": sta.get("wifi_tx_retries_percentage"),
    }
    if brief["band"] == "2.4G" and brief["channel"] is None and sta.get("channel") is None:
        brief["channel"] = None
    return brief


def signal_buckets(clients: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count wireless clients per signal-strength bucket (inclusive bounds)."""
    counts = {label: 0 for label, _, _ in SIGNAL_BUCKETS}
    for c in clients:
        if c.get("is_wired"):
            continue
        sig = c.get("signal_dbm")
        if sig is None:
            continue
        for label, lo, hi in SIGNAL_BUCKETS:
            if lo <= sig <= hi:
                counts[label] += 1
                break
    return counts


def wlan_brief(entry: Dict[str, Any]) -> Dict[str, Any]:
    cfg = entry.get("configuration") or {}
    stats = entry.get("statistics") or {}
    bands = cfg.get("wlan_bands") or ([cfg.get("wlan_band")] if cfg.get("wlan_band") else [])
    return {
        "name": cfg.get("name"),
        "enabled": cfg.get("enabled"),
        "bands": bands,
        "security": cfg.get("security"),
        "wpa_mode": cfg.get("wpa_mode"),
        "wpa3": cfg.get("wpa3_support"),
        "pmf_mode": cfg.get("pmf_mode"),
        "hidden": cfg.get("hide_ssid", False),
        "guest": cfg.get("is_guest", False),
        "client_count": stats.get("current_client_count"),
        "ap_count": stats.get("current_access_point_count"),
    }


def ap_client_load(aps: List[Dict[str, Any]], clients: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-AP client counts cross-checked from the client table (ap_mac join)."""
    counts: Dict[str, int] = {}
    for c in clients:
        if c.get("is_wired"):
            continue
        counts[c.get("ap_mac")] = counts.get(c.get("ap_mac"), 0) + 1
    out = []
    for ap in aps:
        out.append({
            "name": ap.get("name"),
            "mac": ap.get("mac"),
            "reported_clients": ap.get("num_sta"),
            "wireless_clients_seen": counts.get(ap.get("mac"), 0),
        })
    return out


def controller_sysinfo(sysinfo: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": sysinfo.get("name"),
        "version": sysinfo.get("version"),
        "build": sysinfo.get("build"),
        "uptime_seconds": sysinfo.get("uptime"),
        "timezone": sysinfo.get("timezone"),
        "https_port": sysinfo.get("https_port"),
        "update_available": sysinfo.get("update_available"),
    }
