"""unifi-skill — read-only agent CLI for a self-hosted UniFi Network Controller.

Exit-code contract (0 ok / 2 usage / 10 auth / 12 rejected-no-data / 13
network-server) and the {command, input, data} envelope are shared with the
firecrawl-skill lineage; AGENTS.md forbids changing them without updating
README, skill doc, and tests together.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from . import normalize
from .envelope import (RENDERERS, make_envelope, make_error_envelope,
                       print_json, timestamp_slug)
from .env import Config
from .exitcodes import EXIT_AUTH, EXIT_OK, EXIT_REJECTED, EXIT_USAGE
from .transport import COOKIE_EXPIRED_HINT, ControllerError, data_of, get_json

ENDPOINTS = {
    "devices": "/stat/device",
    "clients": "/stat/sta",
    "sysinfo": "/stat/sysinfo",
    "settings": "/get/setting",
    "wlan_v2": "/wlan/enriched-configuration",  # plain /wlan GET is 404 (rfc D6)
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="unifi-skill",
        description="Read-only CLI for a self-hosted UniFi Network Controller.",
        allow_abbrev=False)
    sub = p.add_subparsers(dest="command", required=True)

    def add(name: str, **kwargs):
        sp = sub.add_parser(name, allow_abbrev=False, **kwargs)
        sp.add_argument("--env-file", help="explicit .env file to load")
        sp.add_argument("--json", action="store_true",
                        help="machine envelope instead of human tables")
        sp.add_argument("--timeout", type=float, help="override UNIFI_TIMEOUT seconds")
        return sp

    add("status", help="per-AP RF report: utilization, conflicts, signal buckets")

    c = add("clients", help="client inventory")
    c.add_argument("--band", choices=["ng", "na", "6e"], help="filter by radio")
    c.add_argument("--ap", help="filter by AP name or IP")
    c.add_argument("--weak", action="store_true", help="only clients at or below -70 dBm")

    add("aps", help="AP inventory (name, model, ip, mac, snmp_location, fw)")
    add("wlan", help="SSID list via v2 enriched-configuration")
    add("export", help="timestamped snapshot of all read endpoints")
    return p


def _fetch(cfg: Config, endpoint_key: str, **kw):
    """Single point through which every controller GET flows.

    Patching this one function in tests (instead of ``get_json``) is reliable
    because it is looked up on the module at call time.
    """
    v2 = endpoint_key.endswith("_v2")
    status, payload = get_json(cfg.base_url, ENDPOINTS[endpoint_key], cfg.cookie,
                               timeout=cfg.timeout, tls_verify=cfg.tls_verify,
                               site=cfg.site, v2=v2, **kw)
    return payload


def _load_all(cfg: Config) -> Dict[str, Any]:
    return {
        "devices": data_of(_fetch(cfg, "devices")),
        "clients": data_of(_fetch(cfg, "clients")),
    }


MISSING_BASE_HINT = "UNIFI_BASE_URL is not set. Copy .env.example to .env and fill it in."


def cmd_status(cfg: Config, args: argparse.Namespace) -> Dict[str, Any]:
    raw = _load_all(cfg)
    aps_raw = [a for a in raw["devices"] if a.get("type") == "uap"]
    clients = [normalize.client_brief(s) for s in raw["clients"]]
    ap_names = {a.get("name"): a for a in aps_raw}
    ap_ips = {a.get("ip"): a for a in aps_raw}
    for c in clients:
        ap = ap_names.get(c.get("ap_name")) or None
        # resolve ap_mac -> name for readability
        for a in aps_raw:
            if a.get("mac") == c.get("ap_mac"):
                c["ap_name"] = a.get("name")
                break
    data = {
        "config": cfg.to_dict(),
        "ap_count": len(aps_raw),
        "aps": [normalize.ap_status(a) for a in aps_raw],
        "channel_conflicts": normalize.channel_conflicts(aps_raw),
        "signal_buckets": normalize.signal_buckets(clients),
        "client_count": len(clients),
        "weak_clients": [c["name"] or c["mac"] for c in clients if not c.get("is_wired")
                         and (c.get("signal_dbm") or 0) <= normalize.WEAK_SIGNAL_DBM],
    }
    return {"exit": EXIT_OK, "data": data}


def cmd_clients(cfg: Config, args: argparse.Namespace) -> Dict[str, Any]:
    raw = _load_all(cfg)
    clients = [normalize.client_brief(s) for s in raw["clients"]]
    if args.band:
        clients = [c for c in clients if c.get("band") == normalize.BAND_BY_RADIO.get(args.band, args.band)]
    if args.ap:
        aps_raw = [a for a in raw["devices"] if a.get("type") == "uap"]
        target = next((a for a in aps_raw if a.get("name") == args.ap or a.get("ip") == args.ap), None)
        if target is None:
            raise ControllerError(f"No AP named or addressed '{args.ap}'", EXIT_REJECTED)
        clients = [c for c in clients if c.get("ap_mac") == target.get("mac")]
    if args.weak:
        clients = [c for c in clients if not c.get("is_wired")
                   and (c.get("signal_dbm") or 0) <= normalize.WEAK_SIGNAL_DBM]
    return {"exit": EXIT_OK, "data": {
        "config": cfg.to_dict(), "client_count": len(clients), "clients": clients}}


def cmd_aps(cfg: Config, args: argparse.Namespace) -> Dict[str, Any]:
    raw = _load_all(cfg)
    aps_raw = [a for a in raw["devices"] if a.get("type") == "uap"]
    return {"exit": EXIT_OK, "data": {
        "config": cfg.to_dict(), "ap_count": len(aps_raw),
        "aps": [normalize.ap_brief(a) for a in aps_raw]}}


def cmd_wlan(cfg: Config, args: argparse.Namespace) -> Dict[str, Any]:
    rows = data_of(_fetch(cfg, "wlan_v2")) or []
    return {"exit": EXIT_OK, "data": {
        "config": cfg.to_dict(), "wlan_count": len(rows),
        "wlans": [normalize.wlan_brief(w) for w in rows]}}


def cmd_export(cfg: Config, args: argparse.Namespace) -> Dict[str, Any]:
    out_dir = cfg.export_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp_slug("unifi", "snapshot")
    files: Dict[str, str] = {}
    # All GETs; raw bodies saved untouched — snapshots are local backups and
    # stay in the (gitignored) export dir. Nothing here leaves the machine.
    for name in ("devices", "clients", "sysinfo", "settings", "wlan_v2"):
        payload = _fetch(cfg, name)
        path = out_dir / stamp.replace("snapshot", name.replace("_v2", ""))
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        files[name] = str(path)
    return {"exit": EXIT_OK, "data": {"config": cfg.to_dict(), "exported": files}}


COMMANDS = {"status": cmd_status, "clients": cmd_clients, "aps": cmd_aps,
            "wlan": cmd_wlan, "export": cmd_export}


def _human_input_view(args: argparse.Namespace, cfg: Config) -> Dict[str, Any]:
    view: Dict[str, Any] = {"command": args.command}
    for k, v in vars(args).items():
        if k in ("env_file", "command") or v is None:
            continue
        view[k] = v
    view["json"] = bool(getattr(args, "json", False))
    return view


def run_command(command: str, args: argparse.Namespace, cfg: Config) -> int:
    cfg.resolve_cookie()
    view = _human_input_view(args, cfg)
    if not cfg.base_url:
        print_json(make_error_envelope(command, view, EXIT_REJECTED, MISSING_BASE_HINT))
        return EXIT_REJECTED
    if not cfg.cookie:
        print_json(make_error_envelope(command, view, EXIT_AUTH, COOKIE_EXPIRED_HINT))
        return EXIT_AUTH
    try:
        result = COMMANDS[command](cfg, args)
    except ControllerError as exc:
        print_json(make_error_envelope(command, _human_input_view(args, cfg),
                                       exc.exit_code, str(exc), exc.http_status))
        return exc.exit_code

    envelope = make_envelope(command, _human_input_view(args, cfg), result["data"])
    if args.json:
        print_json(envelope)
    else:
        renderer = RENDERERS.get(command)
        if renderer:
            print(renderer(envelope["data"]))
        else:  # export: no renderer, print the file list
            for name, path in result["data"].get("exported", {}).items():
                print(f"{name}: {path}")
    return result["exit"]


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = Config(env_file=args.env_file)
    if args.timeout:
        cfg.timeout = args.timeout
    if args.command == "help":  # defensive; argparse handles -h
        parser.print_help()
        return EXIT_OK
    try:
        return run_command(args.command, args, cfg)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
