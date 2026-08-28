# Edge gateway — on-site translation box (Pi 4 / mini-PC)

One box per site. It runs the family bridges (Zigbee2MQTT + our canonical bridges) and
typed adapters (Modbus), publishing canonical topics to the site's broker — local during
commissioning, the production EMQX (deploy/emqx/) once the site goes live.

## Provisioning (once per box)

1. Flash Raspberry Pi OS Lite (64-bit); enable SSH.
2. `curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER`
3. Copy this repo (or just `deploy/edge/` + `sdk/` + `adapters/` + `schema/`) to the box.
4. `mkdir config` and copy in the site's bridge configs (start from `adapters/*.yaml`);
   set the broker host, and the EMQX **edge token** as the MQTT password once on EMQX.
5. Plug in the radios/converters and pin their paths (udev): ZB-GW04 → `ZIGBEE_DONGLE`,
   USB-RS485 → `RS485_ADAPTER`. `ls -l /dev/serial/by-id/` gives stable names.
6. Enable what the site needs:
   `SITE=acme-lagos docker compose --profile zigbee --profile tasmota --profile modbus up -d`

## Outage behaviour (the "zero data loss" gate)

- **Broker unreachable**: SDK-based adapters (Modbus meter) spool readings to the
  `spool` volume on disk and replay them in `seq` order marked `q:stale` on reconnect —
  surviving process restarts and power cuts, not just network blips.
- **Zigbee/Tasmota devices**: their last state is retained by the broker; the bridges
  are stateless translators, so they resync from the device's next report.
- **Reboot**: everything is `restart: unless-stopped` under Docker, which starts on boot.

## Zigbee2MQTT settings that matter

In the z2m data volume's `configuration.yaml`: set `availability: true` (drives
`$online`), the site's MQTT broker + edge token, and `permit_join: false` after
commissioning. Pair Aqara sensors close to the coordinator (see catalog notes).
