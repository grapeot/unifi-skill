# Test plan — unifi-skill

## Tiers

1. **Unit (default, offline).** `tests/test_unit.py` — no network, no
   controller, no credentials. Runs in CI on every push. Covers:
   - fixture presence + anonymization self-check (forbidden-pattern scan and
     locally-administered-MAC scan over `fixtures/*.json`)
   - exit-code mapping (`map_http_exit_code` full contract incl. None/network)
   - `.env` loader (no override of real env) and cookie loader (missing file,
     pasted `Cookie:` prefix strip)
   - `Config.to_dict()` never contains the cookie value
   - normalization from real-shape fixtures: `ap_status` (CU self/external
     arithmetic), `channel_conflicts`, `client_brief` + `signal_buckets`
     (inclusive bounds), `wlan_brief` (no passphrase projection),
     `controller_sysinfo`
   - read-only source scan (no POST/PUT/DELETE/PATCH literals in `src/`)
   - CLI end-to-end through `main()` with fake fetch: status envelope,
     missing base URL → 12, missing cookie → 10, argparse usage error → 2
2. **Integration (opt-in, live).** `tests/test_integration.py` — runs only
   with `RUN_UNIFI_INTEGRATION=1` plus `UNIFI_BASE_URL`/`UNIFI_COOKIE_FILE`
   pointing at a reachable controller; skips cleanly otherwise. Exercises the
   five subcommands against real state and asserts envelope shape + exit 0.

## Manual verification checklist

- `status` against a live controller: CU self+external equals total per
  radio; conflicts list matches a manual eyeball of channel assignments.
- Expired cookie → exit 10 (test by pointing `UNIFI_COOKIE_FILE` at `/dev/null`
  and at a stale value).
- `export` writes five timestamped files under the export dir; dir is
  gitignored; nothing printed on stdout except the file list (human) or
  envelope (`--json`).
- Human tables render aligned for wide/empty fields (clients with no
  hostname are blank, not `None`).

## Live verification record (2026-10-03)

- 17 offline unit tests passed.
- Live controller smoke: `status`, `clients`, `clients --band 6e`,
  `clients --weak`, `aps`, `wlan`, `export` all exit 0 against a real
  controller (5 APs, ~38 clients); bad cookie → exit 10.
- During smoke: `signal_buckets` initially over-counted `strong`
  (bucket-bound bug) — fixed to inclusive bounds and regression-tested;
  `weak_clients` listed anonymous clients as `None` — fixed to fall back to
  MAC. Both recorded in `docs/working.md`.

## CI

`.github/workflows/ci.yml`: ubuntu-latest matrix (Python 3.9 / 3.12), `uv
venv` + `uv pip install -e '.[dev]'`, run unit tier only (integration tier
skips by absence of opt-in env; no secrets configured).
