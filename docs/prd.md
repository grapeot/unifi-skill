# PRD — unifi-skill

Status: accepted (2026-10-03). Working language: English.

## Problem

Self-hosted UniFi controllers expose rich live RF telemetry (per-radio
channel utilization, retransmit rates, client signal, per-AP association),
but the web GUI hides the parts that matter for diagnosing a home network:
the split between your own traffic and neighbours' interference, channel
reuse among your own APs, and the tail of weak clients. Coding agents asked
to help with home-Wi-Fi problems can only guess at this state, or are given
tools that can also mutate a running family network — an unacceptable blast
radius for an autonomous agent.

## Users

1. AI coding agents that observe network state, reason about it, and propose
   GUI changes for a human to apply (primary).
2. Homelab humans auditing RF state from a terminal, and keeping timestamped
   config snapshots around GUI edits (secondary).

## Requirements (MVP)

- **R1 — status subcommand.** Per-AP per-radio report: band, channel, width,
  channel utilization split self-generated vs external, retransmit %, client
  count; plus co-channel reuse across the user's own APs and a client
  signal-strength bucket summary (strong ≥-60 / medium / weak / very-weak
  below -80 dBm).
- **R2 — clients subcommand.** Client inventory (name, vendor OUI, band,
  channel, signal, SSID, associated AP) with filters `--band ng|na|6e`,
  `--ap <name|ip>`, `--weak` (≤ -70 dBm).
- **R3 — aps subcommand.** AP inventory: name, model, ip, mac,
  `snmp_location` (the only human-settable placement field on this
  controller generation), firmware, state.
- **R4 — wlan subcommand.** SSID list via the v2
  `wlan/enriched-configuration` endpoint: bands, security, WPA3, PMF,
  hidden/guest flags, live client and AP counts. Passphrases are never
  rendered.
- **R5 — export subcommand.** Timestamped snapshot of every read endpoint to
  `UNIFI_EXPORT_DIR` (default `./tmp/unifi-backups/`). Snapshots stay local;
  the directory is gitignored.
- **R6 — read-only by construction.** The transport exposes a single GET
  primitive; no code path can issue a state-changing request. Enforced by a
  source-scanning unit test, not documentation alone.
- **R7 — output contract.** `--json` prints the `{command, input, data}`
  envelope; errors always print an error envelope carrying `exit_code`,
  `exit_meaning`, and upstream detail. Cookie values are never included in
  any output.
- **R8 — exit codes.** 0 ok / 2 usage / 10 auth / 12 rejected-no-data /
  13 network-server. Expired session cookie must map to 10 whether the
  controller answers 401/403 or 200-with-login-page.
- **R9 — offline-testable.** Unit tests run with no network and no
  controller, against anonymized fixtures. Integration tests are opt-in
  (`RUN_UNIFI_INTEGRATION=1`).
- **R10 — anonymized fixtures.** Fixtures are produced by an anonymizing
  capture script (SSID/AP names → generic, MACs → locally-administered
  pseudo, LAN IPs → TEST-NET ranges, secrets → REDACTED, timestamps →
  shifted) with a `_meta` note per file.

## Non-goals (MVP)

- Any write capability: SSID/RF/firmware/client-blocking/`snmp_location`
  edits — deliberately excluded (see R6 and RFC D3); a human applies changes
  in the controller UI.
- UniFi cloud controllers (`unifi.ui.com`) and UniFi Protect cameras.
- UniFi OS admin surfaces (users, updates, backups).
- Continuous monitoring / dashboards / alerting.
- Official UniFi API tokens (`/v1/sites`): not enabled on the target
  install (UniFi OS Server ships the legacy+v2 cookie surface only).

## Success criteria

1. Offline unit suite green (normalization from fixtures, exit-code mapping,
   cookie/`.env` loader, read-only source scan, fixture anonymization
   self-check).
2. Live smoke green against a real controller: status/clients/aps/wlan/export
   all exit 0; expired-cookie path exits 10.
3. A public GitHub push contains zero real network identifiers (privacy
   scan zero matches over tracked files; fixtures self-check enforces it in
   CI).
