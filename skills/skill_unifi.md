# Skill: unifi-skill — read-only UniFi controller observation

**Type**: Tool skill
**Use when**: an agent needs to observe a self-hosted UniFi Network
Controller's live state — RF channel congestion, weak clients, AP channel
conflicts, SSID configuration, config snapshots — as part of diagnosing or
tuning a home network. **Never** for making changes: this tool cannot change
anything, and agent sessions should propose controller edits to the human,
not apply them.

## Install / setup

Repo: `https://github.com/grapeot/unifi-skill` (master). `uv venv && uv pip
install -e .` → commands `unifi-skill` (alias `unifi`). Config via `.env`
(copy `.env.example`); the session cookie is exported by a human — see
`docs/cookie_refresh.md`. Private handles (controller IP, cookie path) live
only in the local `.env`; never write them into workspace skill files.

## Commands

```bash
unifi-skill status [--json]          # per-AP RF: CU split, retries, conflicts, signal buckets
unifi-skill clients [--json] [--band ng|na|6e] [--ap NAME|IP] [--weak]
unifi-skill aps [--json]             # inventory incl. snmp_location (placement field)
unifi-skill wlan [--json]            # SSIDs: bands/security/WPA3/client counts
unifi-skill export                   # timestamped local snapshot of all read endpoints
```

## Output contract

`--json` (and all errors) → single envelope `{command, input, data}` on
stdout. Exit codes: **0** ok / **2** usage / **10** auth (cookie expired →
tell the human to refresh, see docs/cookie_refresh.md; it is a human step) /
**12** rejected-no-data (bad flag/AP name, controller rejection) / **13**
network/TLS/timeout (controller offline? wrong `UNIFI_BASE_URL`?).

## How to reason with this data

- `status` gives per-radio `channel_utilization_pct` (0–100 airtime busy)
  with `cu_self_pct` (your clients) vs `cu_external_pct` (neighbours,
  microwaves, etc.). High self → your devices/clients are the problem
  (migrate clients to a cleaner band/SSID, reduce chatty devices); high
  external → the channel itself is dirty (change channel, not your devices).
- `channel_conflicts` = your own APs sharing band+channel (co-channel).
  Sometimes deliberate (planned reuse across floors); flag it, don't assume
  it's a bug.
- `signal_buckets` and `clients --weak` find clients at coverage edge; a
  weak client on a congested radio is usually better steered by SSID/band
  choice than by power increases.
- `aps` shows `snmp_location` — the human-authored placement field. If it is
  empty on a multi-AP home, ASK the human which AP lives in which room
  before drawing any location-dependent conclusion; device names (e.g. a
  camera named "Upstairs patio") describe the device, not the AP's floor.

## Pitfalls (real, from development)

- After any AP change made in the UI, CU/retries are distorted ~30 min
  (warm-up). Never baseline inside that window.
- `wlan` passphrases are never output; if you need a passphrase, it's a
  human/GUI matter.
- The v2 SSID endpoint is `wlan/enriched-configuration`; a plain `wlan` GET
  404s (the tool handles this; don't hand-roll requests to this API).
- Read-only by construction: no POST/PUT/DELETE exists in the package. Do
  not "fix" this. Write proposals → `docs/rfc.md` D10.
