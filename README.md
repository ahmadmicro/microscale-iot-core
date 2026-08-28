# Microscale iot-core

The Layer-2 IoT integration platform: **sensor → canonical envelope → retained MQTT topic →
mobile dashboard → time-series history**, on standard infrastructure (MQTT), with device control.
The compounding asset behind Microscale's IoT services line — one canonical schema (frozen v1.0),
a reusable adapter/bridge library, a qualified device catalog, a secured production broker
profile, a data platform with carbon-aware rollups, and an open mobile dashboard.

Proprietary — © Microscale Labs (see `LICENSE`). Clients receive a licence to use the schema,
adapters, and their catalog benchmarks within their own platform; Microscale retains the library.
The `dashboard/` may later be split out under an open licence as a differentiator.

## Documentation

**[docs/](docs/README.md) — guides by audience:** [using the dashboard](docs/user-guide.md) (no
expertise) · [commissioning a site](docs/installer-guide.md) (field tech) ·
[running the platform](docs/operations-guide.md) (sysadmin) ·
[building on it](docs/developer-guide.md) (developer). Background:
[how real devices connect](docs/device-integration.md) · [OTA design](docs/ota.md) ·
[ROADMAP](ROADMAP.md).

## What's here

```
schema/canonical/     the contract: metrics.yaml (FROZEN v1.0) + reading/device JSON Schemas
sdk/iotcore/          adapter base (+ Modbus base, disk store-and-forward) — the reusable heart
adapters/             simulator · Tasmota bridge · Zigbee2MQTT bridge · Orno WE-516 Modbus adapter
catalog/              qualified device catalog — one record per benched model (gate, quirks, sourcing)
dashboard/            open, mobile-first PWA — validated declarative spec, live cards + history charts
historian/            data platform: MQTT→TimescaleDB sink + read API with carbon rollups
deploy/               dev Mosquitto · production EMQX (JWT+ACL) · edge-gateway packaging · historian
tools/                dashboard build/validate/theming · EMQX JWT minting
tests/                schema contract + end-to-end suites for every adapter family (CI-run)
```

## Run the demo (3 terminals, ~2 min)

```bash
# 0. from the repo root
python3 -m venv .venv && . .venv/bin/activate && pip install -r sdk/requirements.txt

# 1. broker
cd deploy && docker compose up -d && cd ..

# 2. simulated devices  →  publishes canonical telemetry to the broker
python -m adapters.simulator

# 3. dashboard  →  open http://localhost:8080 on your phone or browser
cd dashboard && python3 -m http.server 8080
```

You should see live cards (load, voltage, source grid⇄generator, diesel level) updating each
second, and the **Water Heater** toggle switching the simulated plug — the command travels
`dashboard → …/device.relay/cmd → simulator`, and the new state echoes back on
`…/device.relay`, flipping the toggle from the data, not the tap.

Optional — history charts and the data API (the *History* tab needs these):

```bash
docker run -d --rm --name tsdb -p 5432:5432 -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=iotcore timescale/timescaledb:latest-pg16
pip install -r historian/requirements.txt
python -m historian.sink &                      # ms/# → TimescaleDB
uvicorn historian.api:app --port 8090           # the read API the charts call
```

## Verify without the browser

```bash
docker exec iotcore_broker mosquitto_sub -t 'ms/#' -v                     # envelopes on the wire
docker exec iotcore_broker mosquitto_pub \
  -t 'ms/acme-lagos/plug-01/main/device.relay/cmd' -m '"off"'             # command by hand
curl 'localhost:8090/acme-lagos/energy?device=main-meter&bucket=1+hour'   # carbon rollup
```

## Status

Phases 0–4 of the [ROADMAP](ROADMAP.md) are software-complete and end-to-end tested: canonical
schema frozen (`schema-v1.0`), three adapter families (Tasmota, Zigbee2MQTT, Modbus) verified
round-trip, EMQX JWT+per-topic-ACL security model verified live, edge store-and-forward, the
historian with reconciling carbon rollups, and the themed/role-aware dashboard verified in a
headless browser against the full stack. What remains is hardware: benching the catalog's
candidate devices on real units.
