# ESP32 firmware with the iotcore SDK (path C)

*For developers building Microscale's own hardware — inverters, gensets, custom sensors — or any
ESP32 device that should speak the canonical contract natively, with no adapter or bridge.*

Path C from [device-integration.md](device-integration.md): the device itself publishes
`ms/<site>/<device>/<channel>/<metric>` envelopes. One asset, two revenue lines — the same pipe
serves client integrations and Microscale's own products.

## What a conforming device must do (any language)

If you implement in C/ESP-IDF or Arduino instead of MicroPython, this checklist *is* the spec:

1. Publish readings as canonical envelopes (`{t, v, u?, q?, src?, medium?, seq}`) — **canonical
   units** from `schema/canonical/metrics.yaml`, retained, QoS 1. `t` is real epoch ms measured
   at the device (NTP), never broker receive time.
2. Publish a retained `$meta` descriptor on connect, `$online true` retained, and register an
   MQTT **Last-Will** setting `$online false` so a crash is visible.
3. Keep a per-(metric, channel) monotonic `seq`; buffer readings while offline and replay them
   in order marked `q: "stale"` on reconnect.
4. Subscribe `<point>/cmd` for writable metrics; apply the write to hardware, then publish the
   **actual resulting state** — never echo optimistically.
5. Authenticate to the production broker with a **device-role JWT**
   (`tools/mint_token.py device …`) as the MQTT password — the token only permits the device's
   own subtree, which is your blast-radius containment if a device is compromised.

The MicroPython module below implements 1–4; 5 is a constructor argument.

## MicroPython quick start

What's in [`../firmware/micropython/`](../firmware/micropython/):

```
iotcore.py            the SDK: envelope building, $meta/$online+LWT, seq, offline buffer,
                      /cmd dispatch, reconnect-with-backoff run loop  (~180 lines, audit it)
iotcore_registry.py   GENERATED from metrics.yaml (devices can't parse YAML) — regenerate
                      with `python tools/gen_fw_registry.py` after any registry change
examples/smart_plug.py  complete firmware for a metering plug with a controllable relay
```

On a board:

1. Flash MicroPython (≥1.20) from micropython.org; connect with `mpremote`.
2. `mpremote mip install umqtt.simple umqtt.robust` (the MQTT client, from micropython-lib).
3. `mpremote cp firmware/micropython/iotcore.py firmware/micropython/iotcore_registry.py :`
4. Start from `examples/smart_plug.py` → edit `CONFIG` (Wi-Fi, broker, site/device id, JWT for
   production) → `mpremote cp examples/smart_plug.py :main.py` → reset.

The device appears exactly like any adapter-fed device: retained last-known values, `$online`
tracking, dashboard switch round trip, historian ingestion — nothing downstream knows or cares
that it's firmware.

Your firmware supplies two functions: `read() -> {metric: value}` (multi-channel devices key by
`(metric, channel)`) and `on_command(metric, channel, value) -> new_state`, then calls
`device.run(read, interval=5)`. The run loop handles reconnects with exponential backoff and
polls commands between readings.

## The details that bite

- **Time.** `t` must be real epoch ms — call `ntptime.settime()` after Wi-Fi. The module
  auto-corrects MicroPython's port-specific epoch (ESP32 counts from 2000). Without NTP your
  history lands in 2000 and the historian orders it wrong.
- **Offline buffer is RAM** (default 500 readings — at one reading per 5 s per point, hours of
  outage). Oldest readings drop beyond that; on ESP32 there's no disk spool. If the site needs
  longer retention through outages, put an edge gateway there — that's what it's for.
- **TLS:** `MQTTClient(..., ssl=True)` works on ESP32 but check RAM headroom; on a LAN behind
  the site's edge/VPN, plaintext 1883 to the local broker is the normal pattern.
- **Units are law.** Convert in firmware (`read()` returns watts, not kW; W·h, not kWh). The
  registry tuple's unit string is there to double-check against.

## OTA updates

The rollback design is in [ota.md](ota.md) (family C): A/B partitions with a health-gated
commit. On MicroPython, practical OTA is replacing the `.py` files (e.g. `mip` from your own
server, or an mpremote deploy step) — fine for prototypes and commissioning. **Production
firmware with the full A/B rollback guarantee is the ESP-IDF (C) port** — planned, with
milestones and preconditions in [esp-idf-port-plan.md](esp-idf-port-plan.md); the contract
checklist above is its spec, and the `device.fw_*` control metrics are deferred to schema v1.1.

## Testing without hardware

The module runs on MicroPython's **unix port** against your dev broker — the CI-tested path
(`tests/test_firmware.py`):

```bash
cd firmware/micropython
micropython -c "import mip; mip.install('umqtt.simple')"   # once
micropython examples/smart_plug.py   # after pointing CONFIG at localhost & stubbing wifi_up
```

`tests/test_firmware.py` runs the SDK's full contract (envelopes, $meta, LWT wiring, stale
replay, command echo) under CPython with a fake MQTT client — no hardware, no broker — and
fails if `iotcore_registry.py` drifts from `metrics.yaml`.
