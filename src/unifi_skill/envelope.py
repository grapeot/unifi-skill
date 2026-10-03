"""Output envelope helpers (shared lineage: {command, input, data}).

Human tables print to stdout by default; ``--json`` switches to the envelope.
Errors ALWAYS print one envelope (status=error) so agent callers get machine-
readable failure detail regardless of mode.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from typing import Any, Dict, Optional

from .exitcodes import exit_meaning


def make_envelope(command: str, input_: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    return {"command": command, "input": input_, "data": data}


def make_error_envelope(command: str, input_: Dict[str, Any], exit_code: int,
                        message: str, http_status: Optional[int] = None) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "status": "error",
        "exit_code": exit_code,
        "exit_meaning": exit_meaning(exit_code),
        "error": message,
    }
    if http_status is not None:
        data["http_status"] = http_status
    return {"command": command, "input": input_, "data": data}


def print_json(payload: Dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=False)
    sys.stdout.write("\n")


def _fmt_table(headers, rows, widths=None):
    """Tiny dependency-free table renderer."""
    cols = list(range(len(headers)))
    def cell(w, r, i):
        v = r[i] if i < len(r) else ""
        v = "" if v is None else str(v)
        return v
    eff = widths or [max(len(headers[i]), 10) for i in cols]
    lines = ["  ".join(h.ljust(eff[i]) for i, h in enumerate(headers)).rstrip()]
    lines.append("  ".join("-" * eff[i] for i in cols))
    for r in rows:
        lines.append("  ".join(cell(eff, r, i).ljust(eff[i]) for i in cols).rstrip())
    return "\n".join(lines)


def render_status(data: Dict[str, Any]) -> str:
    out = []
    out.append(f"Controller config: {data.get('config', {}).get('base_url')}")
    for ap in data.get("aps", []):
        out.append("")
        loc = f" [{ap['snmp_location']}]" if ap.get("snmp_location") else ""
        out.append(f"### {ap.get('name')} ({ap.get('model')}) {ap.get('ip')}{loc}  clients={ap.get('clients')}  fw={ap.get('firmware')}")
        rows = []
        for r in ap.get("radios", []):
            rows.append([
                r.get("band"), f"ch{r.get('channel')}", f"{r.get('channel_width_mhz')}MHz",
                f"{r.get('clients')} st",
                f"CU {r.get('channel_utilization_pct')}% (self {r.get('cu_self_pct')}% / ext {r.get('cu_external_pct')}%)",
                f"retries {r.get('retries_pct')}%",
            ])
        out.append(_fmt_table(["band", "chan", "bw", "load", "channel utilization", "quality"], rows,
                              widths=[5, 6, 8, 6, 44, 16]))
    conflicts = data.get("channel_conflicts") or {}
    if conflicts:
        out.append("")
        out.append("Co-channel reuse across your own APs:")
        for band, chans in conflicts.items():
            for ch, names in chans.items():
                out.append(f"  {band} ch{ch}: {', '.join(names)}")
    else:
        out.append("")
        out.append("No co-channel reuse between your own APs.")
    buckets = data.get("signal_buckets") or {}
    out.append(f"Client signal buckets: {json.dumps(buckets)}")
    return "\n".join(out)


def render_clients(data: Dict[str, Any]) -> str:
    rows = []
    for c in data.get("clients", []):
        rows.append([c.get("name"), c.get("vendor"), c.get("band"), f"ch{c.get('channel')}",
                     f"{c.get('signal_dbm')}dBm", c.get("essid"), c.get("ap_name") or c.get("ap_mac")])
    return _fmt_table(["name", "vendor", "band", "chan", "signal", "ssid", "ap"], rows,
                      widths=[24, 24, 5, 5, 9, 16, 20]) + f"\n{data.get('client_count')} client(s)"


def render_aps(data: Dict[str, Any]) -> str:
    rows = []
    for a in data.get("aps", []):
        rows.append([a.get("name"), a.get("model"), a.get("ip"), a.get("mac"),
                     a.get("snmp_location"), a.get("firmware"), a.get("state")])
    return _fmt_table(["name", "model", "ip", "mac", "location", "firmware", "state"], rows,
                      widths=[16, 14, 15, 18, 22, 16, 8]) + f"\n{data.get('ap_count')} AP(s)"


def render_wlan(data: Dict[str, Any]) -> str:
    rows = []
    for w in data.get("wlans", []):
        rows.append([w.get("name"), "+".join(w.get("bands") or []),
                     "on" if w.get("enabled") else "off",
                     w.get("security"), "wpa3" if w.get("wpa3") else "",
                     "hidden" if w.get("hidden") else "", "guest" if w.get("guest") else "",
                     w.get("client_count")])
    return _fmt_table(["ssid", "bands", "state", "security", "wpa3", "flags", "", "clients"], rows,
                      widths=[20, 10, 5, 10, 5, 6, 5, 7])


RENDERERS = {"status": render_status, "clients": render_clients,
             "aps": render_aps, "wlan": render_wlan}


def timestamp_slug(prefix: str, seed: str) -> str:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(ch if ch.isalnum() else "-" for ch in seed).strip("-").lower()[:40] or "snapshot"
    return f"{prefix}_{ts}_{safe}.json"
