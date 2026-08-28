# OTA firmware update with rollback — per-family design

A stated production requirement (Phase 2). OTA is inherently firmware-side, so this
document fixes the mechanism per device family; the common principle is **never update
in place without a way back**.

## Family A — Tasmota (Wi-Fi plugs, flashed bridges)

Tasmota ships with OTA built in; our leverage is doing it **fleet-wide over MQTT**:

- Host vetted firmware on the edge gateway or an internal server; set it once with
  `OtaUrl` (persisted per device).
- Fleet upgrade = publish `cmnd/tasmotas/Upgrade 1` (the shared `tasmotas` group topic),
  or per device via its own `cmnd/<topic>/Upgrade`.
- **Rollback**: Tasmota's `.bin.gz` upgrade path keeps the bootloader + a minimal
  safeboot partition (`tasmota32-safeboot`); a failed flash drops the device into
  safeboot, from which the previous pinned image is re-flashed from `OtaUrl`. Keep the
  previous version's binary permanently available at a versioned URL — never overwrite
  `latest` in place.
- Gate: upgrade one canary device per site, watch its `$online` + telemetry for 24 h,
  then roll the fleet.

## Family B — Zigbee end devices (via Zigbee2MQTT)

- Z2M exposes OTA over MQTT: `zigbee2mqtt/bridge/request/device/ota_update/check` and
  `.../update` per device; progress is reported on the response topic.
- Images come from the vendor via the Z2M OTA index; **Aqara rarely publishes images**,
  so treat Zigbee sensor firmware as effectively immutable — pin what you bench.
- **Rollback** is only possible where the vendor ships downgrade images (rare). Policy:
  never OTA a benched Zigbee device mid-engagement unless fixing a live defect; re-bench
  after any update (firmware changes reporting behaviour — catalog entries note this).

## Family C — our own firmware (`iotcore` SDK on ESP32, path C) — the real requirement

Dual-partition A/B with health-gated commit, standard ESP-IDF machinery:

1. Firmware downloads the new image into the inactive OTA partition (esp_https_ota from
   the edge gateway's firmware store), reboots into it **pending verification**.
2. The new image must pass a health gate before `esp_ota_mark_app_valid…` is called:
   broker connected, `$meta` published, and N clean minutes of telemetry (no watchdog
   resets). Until then the bootloader's rollback flag stands.
3. Any crash/watchdog reset before the gate → bootloader boots the previous partition
   automatically. That is the rollback guarantee, and it needs no cloud round-trip.

Control plane (schema v1.1 additions, deferred until this firmware exists — the v1.0
freeze forbids adding them silently):
- `device.fw_version` (state, read) — current running version, also in `$meta`.
- `device.fw_update` (state, writable) — command topic carries the target version/URL.

## Common policy

- Firmware binaries are versioned artifacts in the engagement record (catalog notes the
  benched version); a device's running version must be observable remotely.
- Updates roll site-by-site with a canary; never all sites at once.
- The edge gateway's own stack updates via `docker compose pull && up -d` (compose
  images pinned by tag, previous tag kept for instant downgrade).
