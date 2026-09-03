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

## 2. Stand it up

`deploy/cloud/deploy.sh` automates everything below; each command is safe to re-run.

```bash
git clone <this repo> && cd microscale-iot-core/deploy/cloud
./deploy.sh init --domain iot.example.com --email ops@example.com
./deploy.sh up
```

`init` writes `.env` (mode 600) with a freshly generated `JWT_SECRET`, `PG_PASSWORD`,
`API_TOKEN`, and the sink's own `service` credential — it mints that JWT with nothing but
openssl, so a bare box needs no Python to come up. It **refuses to overwrite an existing
`.env`**, because replacing `JWT_SECRET` invalidates every credential already issued to
clients. `up` builds, starts, and waits for `/api/health`.

Copy `.env` into your secret manager now: it is gitignored and nothing else backs it up.
Losing `JWT_SECRET` means reissuing every client credential.

<details><summary>Doing it by hand instead</summary>

`cp .env.example .env`, then fill `JWT_SECRET` (`openssl rand -hex 32`), `PG_PASSWORD`
(`openssl rand -hex 24`), and `SINK_TOKEN`
(`python tools/mint_token.py service --secret "$JWT_SECRET"` — the `service` role reads
every site and writes nothing). Then `docker compose up -d --build`.
</details>

## 3. TLS

```bash
./deploy.sh tls
```

Checks that port 80 answers on your domain (certbot validates over the same path), issues the
certificate, flips `NGINX_TEMPLATE_DIR` to the HTTPS templates, and recreates nginx. Then add
the renewal to cron:

```
0 3 * * *  /path/to/deploy/cloud/deploy.sh renew
```

**Client dashboards must be built with `wss://` and `https://` URLs** — a page served over
HTTPS cannot open a `ws://` socket. `deploy.sh site` derives the right base URL for you.

## 3b. Behind a web server that is already there

If the box already runs nginx/Apache for other sites, it owns 80/443 and this stack must sit
behind it — binding those ports twice would break whatever is already serving.

```bash
./deploy.sh init --behind-proxy --domain iot.example.com
./deploy.sh up
```

That binds the stack's nginx to **127.0.0.1:8880** only, and records `PUBLIC_BASE` so the links
`deploy.sh site` prints are the ones clients actually use. Then, on the host, add a vhost for the
domain pointing at `127.0.0.1:8880` and let the host's own certbot hold the certificate —
`deploy/cloud/nginx/host-proxy.conf.sample` is that vhost, and `./deploy.sh tls` will refuse in
this mode (the ACME challenge must be answered by whoever owns port 80).

The container nginx already routes `/s/<site>/`, `/mqtt` and `/api/` internally, so the host
vhost is a single `proxy_pass` — but it must forward the WebSocket upgrade headers, or the
dashboard's MQTT connection silently fails while the page still loads.

## 4. Day-to-day commands

| Command | Does |
|---|---|
| `./deploy.sh status` | containers, TLS mode, onboarded sites, health, secret fingerprint |
| `./deploy.sh logs [service]` | tail logs |
| `./deploy.sh backup` | `pg_dump` the history database into `./backups`, keeping the newest 14 |
| `./deploy.sh restore FILE` | load a dump into a **new** database (never over the live one) |
| `./deploy.sh restart [service]`, `./deploy.sh down` | stop/start; `down` keeps data volumes |

## 5. Onboard a client (repeat per site)

Write the site's dashboard spec (start from `dashboard/site.example.yaml`; set `broker:
wss://<domain>/mqtt` and `api: https://<domain>/api`), optionally a theme file, then:

```bash
cd deploy/cloud
./deploy.sh site acme-lagos --spec sites/acme-lagos.yaml --theme ../../dashboard/themes/acme.yaml
```

(It reads `JWT_SECRET` and `DOMAIN` from `.env`, and passes the secret through the environment
rather than the command line, where `ps` would expose it. Keep site specs under the repo —
`deploy/cloud/sites/` is gitignored for that purpose.)

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

- **Backups:** `./deploy.sh backup` nightly (cron), and **copy the files off the box** — they
  land next to the data they protect. Retained broker messages and edge spools are recovery
  buffers, not archives. Restore with `./deploy.sh restore <file>`: it loads into a new
  database and prints the rename to promote it. TimescaleDB needs
  `timescaledb_pre_restore()`/`post_restore()` around the load, which the command handles —
  a plain `psql < dump.sql` leaves the hypertable catalog inconsistent.
- **Capacity:** watch database disk first. History grows with points × rate; drop the poll rate or
  add a TimescaleDB retention/compression policy per client tier before you run out.
- **Upgrades:** `git pull && ./deploy.sh up`. Schema v1.0 is frozen — adding
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
cd deploy/cloud
./deploy.sh init --local           # localhost, ports 8880/11883, certs kept out of /etc
./deploy.sh up
./deploy.sh site acme-lagos --spec sites/acme-lagos.yaml   # prints the links + edge token

# feed it simulated devices with the edge token it just printed
MQTT_PORT=11883 MQTT_USERNAME=edge MQTT_PASSWORD=<edge token> python -m adapters.simulator
```

The printed operator link (`http://localhost:8880/s/acme-lagos/?token=…`) opens the real
hosted dashboard against the real broker — the same path a client takes.
