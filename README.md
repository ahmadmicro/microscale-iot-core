# Microscale iot-core

The Layer-2 IoT integration platform: **sensor → canonical envelope → retained MQTT topic →
mobile dashboard**, on standard infrastructure (MQTT), with device control. The compounding asset
behind Microscale's IoT services line — one canonical schema, a reusable adapter/bridge library, a
qualified device catalog, and an open mobile dashboard.

Proprietary — © Microscale Labs (see `LICENSE`). Clients receive a licence to use the schema,
adapters, and their catalog benchmarks within their own platform; Microscale retains the library.
The `dashboard/` may later be split out under an open licence as a differentiator.

- **How real devices connect (read this first):** [`docs/device-integration.md`](docs/device-integration.md)
- **Where this is going:** [`ROADMAP.md`](ROADMAP.md)
- **Design rationale:** the `notes/01`–`09` decision notes in the `nodewire` study repo.

The runnable vertical slice (Phase 0 — done) is below; a simulator stands in for real devices.

## What's here

```
schema/canonical/     the contract: metrics.yaml + reading/device JSON Schemas  (the real IP)
sdk/iotcore/          adapter base + schema access — the reusable heart (harvested NodeWire DX)
adapters/simulator.py three simulated devices: energy meter, controllable plug, diesel tank
dashboard/            open, mobile-first PWA — declarative spec bound to canonical MQTT topics
deploy/               Mosquitto for dev (EMQX in production — see deploy/README.md)
tools/                YAML→JSON dashboard compile step
```

## Run it (4 terminals, ~2 min)

```bash
# 0. from iot-core/
python3 -m venv .venv && . .venv/bin/activate && pip install -r sdk/requirements.txt

# 1. broker
cd deploy && docker compose up -d && cd ..

# 2. simulated devices  →  publishes canonical telemetry to the broker
python adapters/simulator.py

# 3. dashboard  →  open http://localhost:8080 on your phone or browser
cd dashboard && python3 -m http.server 8080
```

Open `http://localhost:8080`. You should see live cards (load, voltage, source grid⇄generator,
diesel level) updating each second, and the **Water Heater** toggle should switch the simulated
plug — the command travels `dashboard → …/device.relay/cmd → simulator`, and the new state echoes
back on `…/device.relay`, flipping the toggle from the data, not the tap.

## Verify without the browser

```bash
# watch canonical envelopes on the wire
docker exec iotcore_broker mosquitto_sub -t 'ms/#' -v

# issue a relay command by hand
docker exec iotcore_broker mosquitto_pub -t 'ms/acme-lagos/plug-01/main/device.relay/cmd' -m '"off"'
```

## What this proves (and deliberately defers)

**Proves:** the canonical schema is sufficient to describe real devices; adapters are terse to
write; retained MQTT gives instant last-known + live updates; control round-trips; the dashboard is
schema-driven and needs no backend. **Defers to production:** EMQX with JWT + per-topic ACLs + TLS,
a real Modbus/local-MQTT adapter (the simulator shows the shape), edge buffering to disk, and a
time-series store for historical charts.
