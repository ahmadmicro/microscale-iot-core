# How real devices connect — adapters, bridges, and the infrastructure you actually need

The single most important question for the business: *"To use a real device like a Tuya plug, do I
build an adapter for each one, or can the dashboard connect directly? Is it just broker + dashboard,
or do I hand-code adapters per device?"*

## The one rule that answers everything

**The dashboard only ever speaks the canonical contract** — it binds to
`ms/<site>/<device>/<channel>/<metric>` topics carrying the reading envelope (value + canonical
unit). It has no idea what a Tuya plug, a Modbus meter, or a diesel sensor is. So the real question
is never "can the dashboard talk to the device" — it's **"who publishes canonical topics for this
device?"** Something must translate the device's native format into the canonical schema. That
translator is the *adapter/bridge*, and it is where Microscale's value lives.

```
  DEVICE (native format)  ──►  ADAPTER / BRIDGE  ──►  canonical MQTT topics  ──►  DASHBOARD
  Tuya DPIDs / Tasmota          normalize to             ms/site/dev/ch/metric      (binds to
  JSON / Modbus registers       canonical schema         {v, u, src, q, seq}         canonical only)
```

**Can the dashboard connect to a Tuya device directly? No — and not only because of the schema.**
A stock Tuya device speaks a vendor data model (numeric "datapoints", DPIDs) and by default only
talks to the *Tuya cloud*. It isn't reachable in your schema, on your topics, or even on your
network without a translation step. That step is unavoidable. But — and this is the whole point —
**"unavoidable translation" does NOT mean "hand-code every physical device."**

## You normalize per device *type*, not per device — and often with zero code

There is a spectrum, from most work to least:

| Approach | When | Per-new-device cost |
|----------|------|---------------------|
| **A. Typed adapter** (code) | A protocol/device family with no existing bridge (e.g. a specific Modbus register map) | Write **one** ~20-line adapter per *model*; every unit reuses it via config |
| **B. Generic bridge + profile** (config, no code) | A whole family sharing a format — **Tasmota/ESPHome**, **tinytuya LAN**, generic Modbus | **A config row / profile**, zero code, once the family's bridge exists |
| **C. Native canonical** (no adapter at all) | A device *you* program — your own firmware, or an ESP32 you flash with the `iotcore` SDK | **None** — it publishes canonical topics directly |

The marginal cost of the *next* device in a known family is **a config entry**, not code. One
Tasmota bridge serves every Tasmota plug you ever deploy; one Modbus-profile loader serves every
meter you write a register map for. That is precisely the "a new project becomes ~80% assembly,
~20% new" curve from the strategy model — and the adapter/bridge library **is** the Layer-2 asset
that compounds across engagements.

## Worked example: a Tuya smart plug (Habeeb's Cat-2 case)

Tuya is cloud-locked by default, so the sourcing gate requires an open/local path. Two clean ways:

**Path 1 — flash Tasmota/ESPHome (recommended; most Tuya plugs are ESP-based).**
After flashing, the plug publishes *natively* to your broker, e.g.
`tele/plug01/SENSOR → {"ENERGY":{"Power":1850,"Voltage":232,"Total":125.08}}`.
That is **not** canonical. A single **generic Tasmota bridge** (write once, reuse forever) maps it:

```
tele/+/SENSOR                         →   ms/<site>/<device>/main/electrical.active_power  {v:1850,u:W}
  ENERGY.Power  → electrical.active_power (W)      ms/.../electrical.voltage               {v:232,u:V}
  ENERGY.Voltage→ electrical.voltage (V)           ms/.../electrical.energy                {v:125080,u:W.h}
  ENERGY.Total  → electrical.energy (kWh→W.h)
ms/<site>/<device>/main/device.relay/cmd  →   cmnd/plug01/POWER   (control, reverse direction)
```

Add a second, third, hundredth Tasmota plug → **no code**, just a device row (name, topic prefix).

**Path 2 — keep Tuya firmware, use the LAN local API (tinytuya).**
Extract the device's local key once; a **tinytuya adapter** (one, for the whole tinytuya family)
polls it over the LAN, maps DPIDs → canonical, and publishes. Per-device config = IP + local key +
a DP→metric profile. Still no cloud, still one adapter for the family.

Either way: **one reusable translator per family, then config.** Never one adapter per plug.

## So what infrastructure do you actually need?

Three parts — and the third is not optional for real off-the-shelf hardware:

1. **MQTT broker** — the canonical backbone (Mosquitto for dev; **EMQX** in production for JWT +
   per-topic ACLs + TLS). Can run in the cloud or on the edge gateway.
2. **Dashboard server** — static files; serve from anywhere (even the gateway). Binds to the broker.
3. **A normalization layer** — the adapter(s)/bridge(s) that publish canonical topics for your real
   devices. This runs on an **edge gateway** (a Raspberry Pi / mini-PC at the site) for local-only
   devices — Modbus RS-485, LAN Tuya, Tasmota on the site Wi-Fi — because the "own your data, no
   vendor cloud" gate means normalization happens locally, and the gateway also does the buffering
   and store-and-forward. For a device already publishing canonical topics (your own firmware),
   no gateway is needed.

```
                         ┌─────────── EDGE GATEWAY (Pi / mini-PC at the site) ───────────┐
  Modbus meter ──RS485──►│  Modbus adapter ┐                                              │
  Tasmota plug ──WiFi───►│  Tasmota bridge ├─►  canonical topics  ─►  [ MQTT broker ]  ───┼──► Dashboard
  Tuya (LAN)  ──WiFi────►│  tinytuya adapter┘        + local buffer / store-and-forward   │    (phone)
  Your ESP32  ──────────►│  (none: SDK publishes canonical directly) ───────────────────►│
                         └───────────────────────────────────────────────────────────────┘
```

**Bottom line:** broker + dashboard alone is *not* enough for real third-party devices — nothing
would be translating them into your schema. You need the normalization layer. But that layer is
**reusable bridges + declarative config**, hand-coded **once per protocol family**, not per device.
And for hardware you control, you skip it entirely by putting the `iotcore` SDK on the firmware so
it emits canonical topics natively. That asymmetry — cheap third-party onboarding, zero-cost
first-party — is exactly the dual-use leverage the strategy model is built around.

## What exists today vs. what to build

- **Today (the slice):** the SDK proves path **C** (the simulator publishes canonical directly);
  `design/adapters/example_orno_we516.py` sketches path **A** (a typed Modbus adapter).
- **Next (Phase 1, see `ROADMAP.md`):** build the two reusable bridges that cover the devices the
  live engagements actually source — a **generic Tasmota/ESPHome MQTT bridge** (path B, covers the
  Wi-Fi plugs) and **one Modbus meter adapter** (path A, covers DIN-rail sub-metering). After those,
  onboarding a new plug or meter of the same family is a catalog + config entry, not code.
