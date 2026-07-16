# Broker deployment

## Dev (this slice)
```
cd iot-core/deploy
docker compose up -d          # Mosquitto: 1883 (TCP), 9001 (WS)
```
Anonymous, no TLS — local only.

## Production target: EMQX
Swap Mosquitto for EMQX (`emqx/emqx:5`) to get what the slice deliberately defers:
- **Per-topic ACLs** — a client's JWT may read `ms/<their-site>/#` and write only that site's
  `.../cmd` topics. This is the security model that replaces NodeWire's broken auth.
- **JWT authn** — the dashboard and adapters present scoped tokens.
- **TLS** (`8883` TCP, `8084` WSS) fronted by the existing nginx/certbot.
- **Built-in dashboard** on `18083` for ops.

Nothing above the broker changes: the canonical schema, the topic scheme, the adapters, and the
dashboard are all broker-agnostic. This is the whole point of standardizing on MQTT.
