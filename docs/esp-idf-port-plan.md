# Plan — ESP-IDF firmware port (path C, production)

*Status: planned, not started. Deliberately sequenced **after** the cloud work; the interim
answer for client sites is an edge gateway in front of the devices (option 1 below), which is
already built and costs nothing.*

## Why this exists

The MicroPython firmware in [`../firmware/micropython/`](../firmware/micropython/) speaks the
canonical contract and is the right tool for prototypes and commissioning. It cannot, however,
give a device in a client's plant room the one property that matters once we ship our own
hardware: **an update that cannot brick it**. `umqtt` also has no WebSocket transport, so a
MicroPython device cannot use the `wss://` front door the gateways use.

This port is therefore justified by **firmware lifecycle, not connectivity** — a bad update
without rollback means a truck roll to physically reflash, at every site that took it.

### The three options, and when each applies

| | Approach | Cost | OTA rollback | Use when |
|---|---|---|---|---|
| 1 | Devices → LAN MQTT → **edge gateway** → `wss://` cloud | none (built) | n/a (gateway is ours) | **now** — every client engagement |
| 2 | MicroPython + **MQTTS** direct to cloud (8883) | small: EMQX TLS listener, SG rule | no | a site with no gateway and few devices |
| 3 | **ESP-IDF port** (this plan) | 2–3 weeks | yes | shipping our own hardware in volume |

## Preconditions (the trigger to start)

Do not start this off one prototype — the same guard the roadmap puts on Phase 5.

- [ ] Cloud service is stable in production with at least one real client site on it
- [ ] A concrete first device exists (an inverter/genset board we intend to ship), not a hunch
- [ ] Someone owns embedded C maintenance — this is a long-lived artifact, not a spike

## Scope

**In:** an `iotcore` ESP-IDF component implementing the conformance checklist in
[firmware-esp32.md](firmware-esp32.md) (that checklist *is* the spec, and is already frozen),
A/B OTA with health-gated commit per [ota.md](ota.md), an example device app, and the schema
v1.1 additions the control plane needs.

**Out (v1):** Secure Boot and flash encryption (a separate, irreversible-once-burned decision),
anti-rollback fuses, Matter/Thread, and any board-specific driver work beyond the example.

## Milestones

Each has an acceptance test; none is "done" on inspection alone.

### M0 — Spike: answer the two uncertain things (2–3 days)

Everything else is ordinary work; these two are where the risk is.

1. **`wss://` + JWT against the real broker.** One ESP32 publishes a single valid envelope to
   `wss://iot.microscale.net/mqtt`, authenticated with a device-role JWT, verified against
   `esp_crt_bundle` (no pinned cert).
   *Accept:* the reading appears via the public read API.
2. **Rollback actually fires.** Partition table with `ota_0`/`ota_1`/`otadata` and
   `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`. Flash a deliberately-crashing image as the update.
   *Accept:* the device reboots into the **previous** image unattended, and says so on
   `device.fw_version`.

If M0 fails or drags, stop — options 1 and 2 remain available and this plan was cheap.

### M1 — The contract component (4–5 days)

`firmware/esp-idf/components/iotcore/` implementing checklist points 1–5:

- canonical envelope builder against the **generated** registry (extend
  [`tools/gen_fw_registry.py`](../tools/gen_fw_registry.py) to emit a C header, so the frozen
  registry stays the single source of truth for both firmware ports)
- retained `$meta` + `$online` with MQTT Last-Will
- per-`(metric, channel)` monotonic `seq`
- **flash-backed** offline spool replayed as `q:"stale"` — this is a genuine improvement over
  MicroPython, whose buffer is RAM and dies with the power. Wear is bounded because the spool
  is only written while offline; use littlefs on its own partition, not NVS.
- `/cmd` dispatch that publishes the **actual** resulting state, never an optimistic echo

*Accept:* host-target unit tests (`idf.py --preview set-target linux`, Unity) covering envelope
shape, seq monotonicity, stale replay ordering and command echo — mirroring
[`tests/test_firmware.py`](../tests/test_firmware.py), and validating against the same JSON
Schemas so both firmware ports are held to one contract.

### M2 — OTA with the health gate (3–4 days)

- `esp_https_ota` pulling from a versioned firmware store (start with the edge gateway serving
  it; the cloud can host it later)
- health gate before `esp_ota_mark_app_valid_cancel_rollback()`: broker connected, `$meta`
  published, and N clean minutes with no watchdog reset
- **schema v1.1** — a MINOR bump done properly, not slipped in: `device.fw_version` (state,
  read) and `device.fw_update` (state, writable). Registry, JSON Schema, and CI updated in one
  commit; v1.0 consumers keep working.

*Accept:* a canary update rolls forward on a healthy image and rolls back on an unhealthy one,
both observed remotely through `device.fw_version` — no serial cable.

### M3 — Example device + docs (2 days)

A metering-plug app mirroring [`smart_plug.py`](../firmware/micropython/examples/smart_plug.py),
plus a build/flash guide. [firmware-esp32.md](firmware-esp32.md) gains an ESP-IDF section and
stops describing the C port as hypothetical.

### M4 — Field-readiness (2–3 days)

The things that only hurt in year two:

- **Provisioning:** how the device gets its Wi-Fi credentials and its device JWT (NVS write at
  manufacture vs. on-site provisioning). Decide and document.
- **Token expiry — open question.** `mint_token.py device` defaults to 365 days. A fielded
  device whose token expires goes silent and needs a visit. Either mint device tokens long
  (as the historian sink's `service` token already is) or add renewal over MQTT. **Decide
  before the first unit ships**, not after.
- Brown-out and watchdog behaviour on unreliable mains — the actual operating condition.

## Effort

**2–3 weeks** of focused work including bring-up on real hardware, assuming one developer
comfortable in C. The contract is ~180 lines in MicroPython and closer to 600–900 in C, plus
build scaffolding. The estimate excludes board-specific driver work and any Secure Boot
decision.

## Risks

- **Estimate is bring-up-dependent.** Hardware surprises (a flaky USB-UART, an unexpected
  partition layout on an existing board) are the usual reason embedded estimates slip.
- **A second implementation of the contract can drift** from the Python SDK. Mitigation: both
  are generated from / tested against the same frozen registry and JSON Schemas, exactly as
  `tests/test_firmware.py` already does for MicroPython.
- **Secure Boot is one-way.** Burning fuses is irreversible per chip; keep it out of v1 and
  decide deliberately.
