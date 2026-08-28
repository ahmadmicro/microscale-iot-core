# Running the platform

*For whoever operates the cloud side. Assumes: Docker, DNS/TLS, a secrets store.*

The production stack is three compose files on a VPS, plus the per-site edge boxes:

| Piece | Where | What |
|---|---|---|
| Broker | [`../deploy/emqx/`](../deploy/emqx/) | EMQX 5 — JWT auth, per-client topic ACLs |
| Data platform | [`../deploy/historian/`](../deploy/historian/) | TimescaleDB + MQTT→DB sink + read API |
| Dev broker | [`../deploy/`](../deploy/) | Mosquitto, anonymous — local dev ONLY |
| Edge boxes | [`../deploy/edge/`](../deploy/edge/) | installer-managed, one per site |

## Broker + credentials (the security model)

Start EMQX with a strong shared secret (`openssl rand -hex 32`, keep it in your secret store).
There is **no user database**: every client presents a JWT (as its MQTT password) minted with
[`../tools/mint_token.py`](../tools/mint_token.py), and the token's embedded ACL is the whole
policy — anything not whitelisted is denied. Three roles:

```bash
python tools/mint_token.py device    --site acme-lagos --device plug-01 --secret "$S"
python tools/mint_token.py edge      --site acme-lagos --secret "$S"            # gateway boxes, historian sink
python tools/mint_token.py dashboard --site acme-lagos --secret "$S" --days 30  # keep short-lived
```

A device token can only publish its own subtree; a dashboard token can read its site and write
only `/cmd` topics (it cannot fabricate readings); tokens are site-scoped, so client A can never
see client B. Revocation before expiry means rotating the secret (site-wide) — hence short-lived
dashboard tokens. TLS: terminate WSS at nginx with certbot
([`../deploy/emqx/nginx-wss.conf.sample`](../deploy/emqx/nginx-wss.conf.sample)); never expose
plaintext 1883 to the internet; change the EMQX admin UI password and firewall port 18083.

## Data platform

`deploy/historian/docker-compose.yml` runs TimescaleDB, the sink (give it an **edge** token via
`MQTT_USERNAME`/`MQTT_PASSWORD`), and the read API on :8090. Set `API_TOKEN` to require a bearer
token on the API; front it with the same nginx for TLS. Ingest is idempotent — outage replays and
redeliveries don't duplicate rows.

**Back up** the `tsdb-data` volume (`pg_dump` on a cron is fine at current scale). The broker's
retained messages and the edge spools are recovery buffers, not archives — the database is the
record.

## Dashboards

Static files — any web server. Build per site/client with
`tools/build_dashboard.py <site>.yaml --theme <client>.yaml --out-dir <webroot>` and deploy the
`dashboard/` directory plus the built `dashboard.json`/`manifest.webmanifest`. The build fails on
schema violations, so a deployed dashboard can't reference metrics that don't exist.

## Upgrades

- Platform: `git pull`, then `docker compose build && docker compose up -d` per stack. The
  dashboard service-worker cache key is bumped in releases that change the PWA shell, so clients
  pick it up on next load.
- Device firmware: see [OTA](ota.md) — canary one device per site, never all sites at once.
- Schema: v1.0 is **frozen** (tag `schema-v1.0`). New metrics are a minor bump; changing a
  unit/kind is major and breaks consumers — don't.

## Health signals worth watching

- A device's `ms/<site>/<device>/$online` retained flag — false means the device (or its bridge)
  is gone; the broker's Last-Will sets it even on a crash.
- Amber/stale data flowing after an outage is the system working (edge replay), not a fault.
- CI (GitHub Actions) runs the full contract + integration suite on every push — it is the gate
  for schema regressions. (Requires Actions billing to be active on the repo.)
