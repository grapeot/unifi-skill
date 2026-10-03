# unifi-skill

A **read-only** command-line tool for observing a self-hosted UniFi Network
Controller (UniFi OS Server / self-hosted Network Application). It gives
coding agents and homelab users a safe way to inspect home-network RF state —
channel congestion, weak clients, AP channel conflicts, SSID configuration —
with **no capability to change anything**. Configuration changes stay in the
controller's web UI.

## Why

Home Wi-Fi questions ("why is 2.4 GHz congested at night?", "which clients
are on the edge of coverage?", "do two of my APs share a channel?") require
the controller's live telemetry, and the GUI buries it. An AI agent should be
able to *observe* that data and reason about it, but should not be able to
mutate a running family network. This CLI is deliberately read-only by
construction: its transport module only performs GET requests, and a unit
test scans the source to enforce that.

## Install

```bash
git clone https://github.com/grapeot/unifi-skill && cd unifi-skill
uv venv && uv pip install -e .
# or: python3 -m venv .venv && .venv/bin/pip install -e .
```

Requires Python 3.9+, network access to your controller, and no third-party
runtime dependencies (standard library only; `pytest` for development).

## Configure

```bash
cp .env.example .env   # edit values; see docs/cookie_refresh.md for the cookie
```

| Variable | Meaning | Default |
|---|---|---|
| `UNIFI_BASE_URL` | Controller base, e.g. `https://192.0.2.10:11443` | — (required) |
| `UNIFI_COOKIE_FILE` | File containing `TOKEN=...; JSESSIONID=...` | `~/.config/unifi-skill/cookie.txt` |
| `UNIFI_SITE` | Site name in controller paths | `default` |
| `UNIFI_TLS_VERIFY` | `1` to require valid TLS certs | off (LAN self-signed) |
| `UNIFI_TIMEOUT` | Per-request timeout seconds | `30` |
| `UNIFI_EXPORT_DIR` | Where `export` writes snapshots | `./tmp/unifi-backups/` |

Credentials are read from the local environment: a `.env` file (gitignored,
chmod 600) and/or the cookie file. Nothing credential-like is ever printed in
output — envelopes include the cookie *path*, never its value.

## Commands

```bash
unifi-skill status    # per-AP RF report: channel utilization (self vs external),
                      # retransmits, channel conflicts between your APs, signal buckets
unifi-skill clients   # client inventory (--band ng|na|6e, --ap <name|ip>, --weak)
unifi-skill aps       # AP inventory: name, model, ip, mac, snmp_location, firmware
unifi-skill wlan      # SSID list: bands, security, WPA3, hidden/guest, client counts
unifi-skill export    # timestamped JSON snapshot of every read endpoint (local backup)
```

Every command supports `--json` for machine output. Human mode prints tables.

### Reading the RF report

`status` splits per-radio channel utilization into **self** (traffic your own
clients generate) and **external** (neighbours' Wi-Fi and other 2.4/5/6 GHz
sources). A radio at 87% CU with `self 30% / ext 57%` is a neighbours problem,
not your devices — a distinction the GUI does not make visible.

## Output contract

`--json` prints one envelope on stdout:

```json
{ "command": "status", "input": { "...": "flags actually passed" },
  "data": { "config": {"base_url": "...", "cookie_file": "...", "cookie_loaded": true},
            "aps": [], "channel_conflicts": {}, "signal_buckets": {}, "client_count": 0 } }
```

Errors also print one envelope with `data.status = "error"`, `data.exit_code`,
`data.exit_meaning`, and the upstream error detail — enough for an agent to
diagnose without guessing.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | ok |
| 2 | usage error (bad flags) |
| 10 | auth — cookie missing/expired (401/403 or login-page response) |
| 12 | rejected / no data (other 4xx, bad AP name, success=false) |
| 13 | network / TLS / timeout / 5xx |

## For AI agents (installing this skill)

Hand this repository's URL to Codex, Claude Code, Cursor, OpenCode, or
another coding agent and ask it to install the skill. The agent should: read
this workspace's `AGENTS.md` (or `CLAUDE.md`) and any routing file such as
`WORKSPACE.md`; clone the repo and run the Install and Configure steps above;
add `skills/skill_unifi.md` to the workspace discovery chain (for example the
workspace `rules/skills/INDEX.md`, or a one-line pointer in `AGENTS.md` if no
index exists). Keep your cookie file at `chmod 600` outside any git repository.
This tool is safe to let an agent run against your controller: it can only
read.

## Development

```bash
uv venv && uv pip install -e '.[dev]'
python -m pytest tests/test_unit.py -q        # offline, fixture-driven
RUN_UNIFI_INTEGRATION=1 python -m pytest tests -q   # live controller (opt-in)
```

See `AGENTS.md`, `docs/prd.md`, `docs/rfc.md`, `docs/test.md`,
`docs/working.md`.

## License

MIT
