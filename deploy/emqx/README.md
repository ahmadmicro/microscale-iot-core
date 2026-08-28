# Production broker — EMQX 5 with JWT auth + per-client topic ACLs

Replaces the dev Mosquitto for hostile-network deployments. The security model
(verified live in `tests/test_emqx_acl.py`):

- **Authentication**: every client presents a JWT as its MQTT password, HMAC-signed with
  the broker's `JWT_SECRET`. No broker-side user database.
- **Authorization**: the token's embedded `acl` claim is a topic whitelist enforced by
  EMQX; everything not whitelisted is denied (`no_match = deny`). So the token both
  authenticates the client and scopes it:
  - a **device** token publishes only `ms/<site>/<device>/#` and subscribes only its own
    `/cmd` topics — a compromised device cannot spoof another device or site;
  - an **edge** token covers `ms/<site>/#` plus the native namespaces its bridges
    translate (`tele/stat/cmnd`, `zigbee2mqtt`);
  - a **dashboard** token reads `ms/<site>/#` and writes only `/cmd` topics — a stolen
    dashboard credential cannot fabricate readings.

## Run

```sh
export JWT_SECRET="$(openssl rand -hex 32)"   # >= 32 bytes — keep it in your secret store
docker compose up -d
```

Mint credentials (see `tools/mint_token.py` for all roles):

```sh
python tools/mint_token.py device    --site acme-lagos --device plug-01 --secret "$JWT_SECRET"
python tools/mint_token.py edge      --site acme-lagos --secret "$JWT_SECRET"
python tools/mint_token.py dashboard --site acme-lagos --secret "$JWT_SECRET" --days 30
```

Clients pass the token as the MQTT password (username is informational). Rotation =
mint a new token; revocation before `exp` requires rotating `JWT_SECRET` (site-wide),
so keep dashboard tokens short-lived.

## TLS

Terminate WSS at nginx in front of port 8083 — `nginx-wss.conf.sample` + certbot.
Port 1883 stays plaintext for LAN/VPN edge traffic only; never expose it publicly.
Change the EMQX dashboard (port 18083) admin password immediately and firewall it.
