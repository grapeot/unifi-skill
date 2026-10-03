# RFC — unifi-skill

Working language: English. Decisions verified against a self-hosted UniFi OS
Server (UniFi OS Server model, Network Application 8.x generation) reachable
behind nginx at a LAN address with a nonstandard HTTPS port.

## D1 — Auth: browser session cookie, not API tokens

UniFi OS Server exposes a newer Network API (`/v1/sites` + API tokens) that
exists but is not enabled on the target install (verified: the endpoint
answers 200 on the root path only; site-scoped token auth surfaces are
disabled). The cookie surface the web UI itself uses (`TOKEN` + `JSESSIONID`
HttpOnly cookies against `/proxy/network/...`) is fully functional, and the
session lifetime (hours) suits agent sessions. Alternative rejected: scraping
the login form to mint cookies programmatically — stores the controller
password on disk, which this tool refuses to touch (read-only includes
credentials: a human logs in and exports the cookie; see
`docs/cookie_refresh.md`).

Consequence: expired cookie is the normal failure mode. Two upstream shapes
must both map to exit 10: an explicit 401/403, and a 200 whose body is the
HTML login page (observed on some generations). The transport detects both.

## D2 — Standard library only

Same lineage decision as firecrawl-skill/tavily-skill: `urllib.request` +
`ssl`, no `requests`. A LAN diagnostic tool should install anywhere with
Python 3.9+ and no dependency resolution surprises.

## D3 — Read-only is an architectural invariant, not a policy

The transport module exposes exactly one request primitive (`get_json`,
`method="GET"` literal). There is no shared function that accepts a method
argument. `test_no_http_write_methods_in_src` scans all source files for
POST/PUT/DELETE/PATCH method literals and fails the build on any hit. Rationale:
an agent-facing tool that observes a family network should make dangerous
capability *unreachable*, not merely discouraged in a README. Writes to
`snmp_location` were explicitly considered (it is the one field a human asked
to set) and rejected: a write path, once added, invites scope creep toward
radio configuration, and the user applies that edit in the UI in seconds.

## D4 — Output envelope and exit codes shared across the skill lineage

`{command, input, data}` with fixed codes 0/2/10/12/13, matching
firecrawl-skill and tavily-skill. Agents that drive several of these tools
share one error-handling reflex: 10 → refresh cookie, 12 → bad argument or
controller rejection, 13 → unreachable. Human tables are the default for
`status`/`clients`/`aps`/`wlan`; `--json` is the agent path. Error output is
always the envelope (even in human mode) — an agent debugging a failure must
never have to parse a table.

## D5 — TLS verification off by default

LAN controllers carry self-signed certificates; requiring verification would
make the default path fail for everyone. `UNIFI_TLS_VERIFY=1` opts in for
users with real LAN certs. Threat-model note: the tool only reads config
state that the network owner already controls; a MITM on the LAN could feed
it fake telemetry but gains nothing credential-like (nothing sensitive is
sent beyond the session cookie, which only works from the LAN anyway and
expires in hours). Documented so users can decide.

## D6 — v2 SSID list lives at `wlan/enriched-configuration`

`GET /v2/api/site/default/wlan` returns 404 on this generation; the
working read path is `/v2/api/site/default/wlan/enriched-configuration`,
which returns `{configuration, details, statistics}` per SSID. The
configuration block includes `x_passphrase`; `normalize.wlan_brief`
deliberately projects only safe fields so a passphrase cannot leak into
rendered output or an error envelope.

## D7 — `radio_table_stats` vs `radio_table`

Device rows carry both. `radio_table` is the *desired* config (channel
`auto`, ht modes, power bounds); `radio_table_stats` is the *live* state
(channel actually in use, `cu_total` / `cu_self_tx` / `cu_self_rx`,
`tx_retries_pct`, `num_sta`). Reading the wrong one silently yields
plausible-looking nonsense (a channel value of `"auto"` where you expected a
number). The CU split is the core diagnostic: `cu_total - (cu_self_tx +
cu_self_rx)` approximates airtime consumed by neighbours and other EM
sources — the difference between "my devices are busy" and "the channel is
dirty", which drives opposite remedies.

## D8 — AP warm-up transient

Immediately after any controller-side AP change (channel switch, SSID
edit, power change) the telemetry reports distorted retransmit/CU figures
for roughly 30 minutes while radios re-associate clients. Baselines and
before/after comparisons taken inside that window are misleading. Agents
must wait or annotate. (Real observed lesson from the parent tuning project.)

## D9 — Fixture anonymization map

`scripts/capture_fixtures.py` rewrites every identifier captured from a live
controller: SSIDs → `home-main`/`home-iot`/`home-6g`; AP names → `AP-N`
(product names like `U6 Enterprise` are product vocabulary and stay); MACs →
`02:00:00:00:00:NN` (locally-administered bit set, stable per original);
LAN IPs (RFC1918 + link-local) → TEST-NET ranges (`192.0.2.x`,
`198.51.100.x`, `203.0.113.x`); client hostnames → `client-N` / `phone-N`;
Mongo ObjectIds → stable pseudo ids; secret-looking fields
(`x_passphrase`, `x_iapp_key`, `psk`, `token`, …) → `REDACTED-*`; epoch
timestamps shifted by a fixed offset. Each fixture carries a `_meta` note.
`test_fixtures_exist_and_are_anonymized` and
`test_real_macs_absent_from_fixtures` fail the offline suite if the map is
ever violated.

## D10 — Future decisions (NOT implemented)

- `location` write (set `snmp_location`): the only plausible narrow write;
  would need a controller-side re-verification of `set/device` semantics on
  this generation and an explicit `--apply` gate. Currently excluded by D3;
  revisit only with a strong user case.
- Continuous sampling (`watch` subcommand) for CU time-series. Deferred:
  belongs with an exporter, not this read CLI.
