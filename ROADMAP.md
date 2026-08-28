# Roadmap

From the working slice to the Layer-2 asset and (only when pulled) the Layer-3 product. Sequenced so
each phase is funded by, and deposits into, the two live engagements (SchimaticBlue energy/carbon
platform; Habeeb home-energy PoC). Discipline from the scalability model applies throughout: every
engagement adds ≥1 benchmarked catalog entry and ≥1 adapter; licence, don't assign; don't build
Layer 3 off a hunch.

## Phase 0 — Vertical slice ✅ DONE
Canonical schema (v1.0 draft) · adapter SDK · 3-device simulator · mobile PWA · MQTT broker.
Verified end-to-end: canonical telemetry flows, retained last-known works, relay control
round-trips, dashboard binds over MQTT-over-WebSocket. **This is the architecture, proven.**

## Phase 1 — First real devices + freeze the contract  ⟵ NEXT
Turn the simulator into real hardware and harden the IP.
- [x] **Generic Tasmota/ESPHome MQTT bridge** (config-driven, no per-device code) — covers the
      Wi-Fi smart plugs (Habeeb Cat-2). One bridge → every flashed plug. See `docs/device-integration.md` path B.
      *Built and verified round-trip against a simulated Tasmota device (`adapters/tasmota_bridge.py`);
      remaining: wire one real flashed plug.*
- [x] **Zigbee family via Zigbee2MQTT** — ZB-GW04 v1.2 USB coordinator on the edge host (works
      stock) and/or flashed ZB-GW03 Ethernet gateway; one generic `zigbee2mqtt` bridge (same
      pattern as the Tasmota bridge) unlocks the in-stock Zigbee switches, terminal blocks, and
      RGB bulbs as config rows. Candidate records: `catalog/zb-gw03.yaml`, `catalog/zb-gw04.yaml`.
      *Bridge built and verified round-trip against simulated Z2M (`adapters/zigbee2mqtt_bridge.py`),
      with Aqara sensor/plug/switch profiles and new canonical metrics (battery, pressure,
      illuminance, contact, leak); remaining: pair real hardware via a coordinator.*
- [x] **One Modbus meter adapter** (typed, e.g. Orno WE-516) — covers electricity sub-metering
      (SBE) and DIN-rail Cat-3. See `design/adapters/example_orno_we516.py`.
      *Built as designed: declarative `ModbusAdapter` base in the SDK + `adapters/orno_we516.py`
      register declaration; verified against a register-accurate Modbus TCP simulator with
      per-phase L1/L2/L3 channels; remaining: verify register map on a physical meter.*
- [x] **Freeze canonical schema v1.0** + add JSON-Schema validation of envelopes/descriptors in CI.
      *Frozen 2026-08-28 (tag `schema-v1.0`) after being battle-tested by three adapter families
      (medium, sensor, and light metrics all landed pre-freeze). `tests/` + GitHub Actions run the
      contract tests and all adapter integration suites against a real broker.*
- [ ] **First catalog entries**: bench each device vs a reference meter → `catalog/<model>.yaml`
      (measured accuracy, procurement, gate results). This is the compounding deposit.
- **Engagement payoff:** the Habeeb PoC "hero deliverable" (live V/A/W/kWh + programmatic relay)
  ships as this, not a throwaway script; SBE gets its first real Modbus sub-meter online.

## Phase 2 — Edge gateway + production security
Make it survive the real world and a hostile network.
- [ ] **Edge-gateway packaging** (Pi / mini-PC): run bridges/adapters on-site; **local buffering to
      disk + store-and-forward + auto-resync** on reconnect (the `seq`/`q:stale` plumbing already exists).
- [ ] **Swap Mosquitto → EMQX**: JWT authn, **per-topic ACLs** (a client token reads `ms/<their-site>/#`,
      writes only its `/cmd`), TLS fronted by nginx/certbot.
- [ ] **OTA firmware update** with rollback (a stated production requirement).
- **Engagement payoff:** satisfies "zero data loss on outage" + "own your pipe, securely" gates.

## Phase 3 — Data platform (the API boundary)
Everything above ingestion; the clean hand-off to analytics.
- [ ] **Time-series store** (TimescaleDB or InfluxDB): a sink subscribing to `ms/#` → history.
- [ ] **History/read API** for dashboard charts and downstream consumers.
- [ ] **Canonical → analytics API** — the stable contract SBE builds EMIS/carbon on (their scope).
- [ ] **Carbon support**: derive Scope 1 (generator fuel) / Scope 2 (grid) from `src` tags + fuel
      metrics; expose per-source energy rollups.

## Phase 4 — Dashboard as a product
Harden the differentiator (the thing nobody ships cleanly: open + mobile + easy).
- [ ] History charts (from Phase 3), **PWA push alerts** (abnormal-consumption threshold → notify).
- [ ] Per-client **theming/branding** build + scoped tokens; multi-page/multi-site.
- [ ] Role-based views; author-time validation of specs against the metric registry.
- [ ] Decide open-source split of `dashboard/` (MIT) vs. proprietary core.

## Phase 5 — Layer 3 product (ONLY when pulled by repetition)
Package the asset as "Plaid/Twilio for energy-monitoring hardware."
- [ ] Adapter library + device catalog + SDK as a licensed/subscribed product.
- [ ] Curated China→Nigeria supply channel for vetted, non-cloud-locked devices.
- **Guard (from the strategy doc):** do NOT build this off one client and a hunch. Two demand
  signals ≠ product-market fit. Ship it only after the adapter-reuse % is high across several
  engagements — let it be *pulled* into existence.

## Cross-cutting, always-on
- **Every engagement** deposits ≥1 catalog entry + ≥1 adapter (contract it in the SOW).
- **Track adapter-reuse %** per new engagement — that number is the scaling curve; when it's high
  and repeatable, Phase 5 is justified.
- **Dual-use:** Microscale's own inverters/generators publish through the *same* canonical pipe via
  the `iotcore` SDK on their firmware (path C) — one asset, two revenue lines.

## Immediate next step
Build the **generic Tasmota bridge** and wire one real flashed plug end-to-end into the existing
dashboard — it's the shortest path from "simulated" to "a real device on a client's phone," and it
directly serves the Habeeb PoC. The Modbus adapter follows for SBE.
