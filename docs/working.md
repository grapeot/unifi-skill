# Working notes — unifi-skill

## Changelog

### 2026-10-03 — scaffold, implementation, live verification

- Scaffolded the repo per the workspace project-scaffold conventions (public
  repo intake gate: this repo is public; fake handles throughout).
- Implemented read-only CLI: `status`, `clients`, `aps`, `wlan`, `export`;
  stdlib-only; GET-only transport with source-scan enforcement.
- 17 offline unit tests green; live smoke against a real controller: all
  subcommands exit 0 (5 APs / ~38 clients), bad-cookie → exit 10.
- Fixed during live verification:
  - `signal_buckets` used exclusive lower bounds and counted every client as
    `strong` (e.g. -81 dBm landed in no bucket, -60..-300 range logic was
    inverted); rewritten to inclusive ordered bounds, regression test added.
  - `weak_clients` surfaced anonymous clients as `None`; falls back to MAC.
  - argparse global `--json` before the subcommand was rejected
    (`parents=[common]` doesn't accept pre-subcommand placement); flags now
    live on each subparser so `unifi-skill status --json` and
    `unifi-skill aps --json` both work.
- Real traps hit while building (see Lessons Learned): v2 wlan list 404,
  `radio_table` vs `radio_table_stats`, 200-with-login-page on expired
  session.

### 2026-10-03 — public launch

- Repo published to GitHub (master), branch protection verified:
  0 required reviewers, admin enforcement on (no bypass), direct pushes to
  master rejected. CI runs the offline tier on Python 3.9/3.12.
- Ecosystem registration entries added in context-infrastructure (zh) and
  context-infrastructure-en (the SKILL_ECOSYSTEM.md links that previously
  404ed now resolve).

## Lessons Learned

- **Expired cookie can look like HTTP 200.** Some controller generations
  answer a stale session with the HTML login page at status 200 instead of
  401. The transport must sniff `<!doctype html` / `<html` bodies and map
  them to exit 10, or agents will try to `json.loads` a login page and
  report "non-JSON response" as a server bug.
- **CU total ≠ your traffic.** `cu_total` minus (`cu_self_tx`+`cu_self_rx`)
  is neighbour airtime; without this split every congested channel looks
  like a self-inflicted problem and the remedy suggestion goes the wrong
  direction. This split is the reason this tool exists.
- **Post-change telemetry lies for ~30 min.** After the controller applies
  AP changes, retransmit/CU figures are warm-up distorted. The parent tuning
  project measured this; do not baseline inside the window (docs/rfc.md D8).
- **`radio_table` is wishes, `radio_table_stats` is reality.** Channel can
  literally read `"auto"` in the former. Normalization must read stats for
  state and table for config, and keep them in separate output fields
  (`radios` vs `radio_configs`) so an agent cannot confuse them.
- **argparse global-then-subcommand is a footgun for subcommand-shared
  flags.** Shared `parents=[common]` parsers only accept the flags *after*
  the subcommand name; putting them on the root parser instead makes
  `status --json` work but `aps --json` fail depending on ordering. For an
  agent-facing CLI, per-subparser flags (accept `--json` where the
  subcommand sits) match how callers actually type.
