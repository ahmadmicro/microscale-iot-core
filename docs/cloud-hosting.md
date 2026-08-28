# Running the Microscale IoT cloud service

*For whoever stands up and operates the hosted platform. Assumes: a VPS, DNS control, Docker,
a secret store. Per-site field work is the [installer guide](installer-guide.md); day-2 concerns
common to any deployment are in the [operations guide](operations-guide.md).*

This turns the components into **one multi-tenant service**: clients' edge gateways publish into
a shared broker, every site's data lands in one history database, and each client gets a branded
dashboard at their own URL — isolated from every other client by the token they hold.

## The shape

```
                         ┌───────────── your VPS (deploy/cloud/) ─────────────┐
  client site A  ──┐     │  nginx :443 ─┬─ /s/<site>/   per-client dashboards │
  (edge gateway)   ├─────┼──────────────┼─ /mqtt        WSS → EMQX            │
  client site B  ──┘     │              └─ /api/…       historian read API    │
  (edge gateway)  1883   │  EMQX ── sink ── TimescaleDB                       │
                         └────────────────────────────────────────────────────┘
```

One box runs it all. Every credential is a JWT signed with a single secret — there is no user
database, and **the token is the tenant boundary**: a site's token can only touch `ms/<site>/…`,
on the broker *and* on the read API.

## 1. Server and DNS

A 2 vCPU / 4 GB VPS handles many sites at typical polling rates; give it disk for history
(TimescaleDB) and put it in the region nearest your sites. Point an A record at it
(`iot.<yourdomain>`). Open **80/443** publicly. Port **1883** (MQTT for edge gateways) should be
restricted to your sites' egress IPs or a VPN — never left open to the internet.

## 2. Configure secrets

```bash
git clone <this repo> && cd microscale-iot-core/deploy/cloud
cp .env.example .env
```

Fill `.env`:

```bash
openssl rand -hex 32     # → JWT_SECRET   (signs every credential — treat as root)
openssl rand -hex 24     # → PG_PASSWORD
```

Then mint the platform's own internal credential and paste it into `SINK_TOKEN`:

```bash
JWT_SECRET=<the secret> python ../../tools/mint_token.py service --secret "$JWT_SECRET"
```

The `service` role reads every site and writes nothing — the least privilege the historian needs.
Store `.env` in your secret manager; it is gitignored, and losing `JWT_SECRET` means reissuing
every client credential.

## 3. First bring-up (HTTP, for the certificate)

```bash
docker compose up -d --build
curl http://<your-domain>/api/health          # {"ok":true}
```

## 4. TLS

```bash
docker compose run --rm --profile certs certbot          # issues the certificate
```

Then switch nginx to the TLS config by pointing its template mount at the HTTPS directory in
`docker-compose.yml`:

```yaml
      - ./nginx/templates-https:/etc/nginx/templates:ro   # was ./nginx/templates
```

`docker compose up -d nginx`. Verify `https://<domain>/api/health`, then add a renewal cron:
`docker compose run --rm --profile certs certbot renew && docker compose restart nginx`.

**Client dashboards must be built with `wss://` and `https://` URLs** — a page served over HTTPS
cannot open a `ws://` socket.

## 5. Onboard a client (repeat per site)

Write the site's dashboard spec (start from `dashboard/site.example.yaml`; set `broker:
wss://<domain>/mqtt` and `api: https://<domain>/api`), optionally a theme file, then:

```bash
JWT_SECRET=<secret> python tools/onboard_site.py acme-lagos \
    --spec sites/acme-lagos.yaml --theme dashboard/themes/acme.yaml --domain iot.example.com
```

That validates and builds the dashboard into `deploy/cloud/dashboards/acme-lagos/` (live
immediately at `https://<domain>/s/acme-lagos/`) and prints three things to hand over:

| Credential | Goes to | Lifetime |
|---|---|---|
| **Edge token** | the site's gateway box (`MQTT_PASSWORD`) — see [installer guide](installer-guide.md) | 1 year |
| **Operator link** | the client's manager — full dashboard incl. switches | 30 days by default |
| **Viewer link** | staff — read-only, operator pages hidden | 30 days by default |

Send links over a private channel: the token is in the URL, and the browser remembers it after
the first open (so a bookmark keeps working without the query string). Reissue by re-running the
command; **revoking one client before expiry means rotating `JWT_SECRET` and reissuing all** —
which is exactly why dashboard tokens are short-lived and edge tokens are per-site.

## What isolates one client from another

Verified end-to-end in [`tests/test_emqx_acl.py`](../tests/test_emqx_acl.py) and the cloud
smoke run:

- A **device** token publishes only its own device subtree — a compromised device cannot forge
  another device's readings, let alone another client's.
- An **edge** token is scoped to one site's `ms/<site>/#`.
- A **dashboard** token reads one site and writes only `/cmd` topics — it cannot fabricate
  readings. Presented to the read API it returns **200 for its own site and 403 for anyone
  else's**.
- Subscribing outside your scope is refused by the broker (`SUBACK 0x80`), not silently ignored.

## Day 2

- **Backups:** the `tsdb-data` volume is the record — nightly `pg_dump` off-box. Retained broker
  messages and edge spools are recovery buffers, not archives.
- **Capacity:** watch database disk first. History grows with points × rate; drop the poll rate or
  add a TimescaleDB retention/compression policy per client tier before you run out.
- **Upgrades:** `git pull && docker compose up -d --build`. Schema v1.0 is frozen — adding
  metrics is a minor bump, changing units is not allowed.
- **Health:** each device's retained `$online` flag (Last-Will makes a crash visible), the sink's
  logs, and `curl https://<domain>/api/health`. If a client reports "everything frozen", check
  their gateway first — the platform being up is one `curl`.
- **Adding a site later** costs one `onboard_site.py` run; nothing about the platform changes.

## Local evaluation

The whole service runs on a laptop before you rent anything — the compose file takes
`HTTP_PORT`/`MQTT_PORT`/`LETSENCRYPT_DIR` overrides and the HTTP template serves everything
unencrypted:

```bash
cd deploy/cloud && cp .env.example .env     # set DOMAIN=localhost, HTTP_PORT=8880, MQTT_PORT=11883
docker compose up -d --build
MQTT_PORT=11883 MQTT_USERNAME=edge MQTT_PASSWORD=<edge token> python -m adapters.simulator
# → http://localhost:8880/s/<site>/?token=<dashboard token>
```
