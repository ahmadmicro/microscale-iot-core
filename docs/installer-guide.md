# Commissioning a site

*For field engineers. Assumes: a terminal, SSH, basic electrical wiring. You should not need to
write any code — if you do, the device belongs in the [developer guide](developer-guide.md) first.*

A site is: **devices** → an **edge gateway box** translating them to canonical MQTT → the
**broker** (yours during commissioning, the production one at handover) → the **dashboard**.

## 0. What you need before going on site

- The site id (e.g. `acme-lagos`) — it prefixes every topic; agree it once, never change it.
- An **edge token** for the production broker (ops mints it — see the
  [operations guide](operations-guide.md)); not needed while testing against a local broker.
- The **broker URL**. Against the hosted service this is `wss://<domain>/mqtt` — WebSocket
  over TLS on port 443, so no MQTT port has to be opened anywhere. Set it as `MQTT_URL`
  for SDK adapters, or `broker: {url: wss://…, username: edge, password: <token>}` in a
  bridge config. `mqtt://host` (plain, LAN only) still works for local commissioning.
- The gateway box (Pi 4 / mini-PC), radios (ZB-GW04 USB dongle for Zigbee), a USB-RS485 adapter
  for Modbus meters, and the devices themselves — check [`../catalog/`](../catalog/) for each
  model's known quirks *before* you're standing in front of it.

## 1. Provision the gateway box

Follow [`../deploy/edge/README.md`](../deploy/edge/README.md) — flash Pi OS, install Docker, copy
the repo, put the site's configs in `deploy/edge/config/`, then enable the services the site needs:

```bash
SITE=acme-lagos docker compose --profile zigbee --profile tasmota --profile modbus up -d
```

Everything restarts on reboot. Readings taken while the broker is unreachable are buffered on
disk and replayed automatically (they show amber/stale on the dashboard, which is correct).

## 2. Connect devices — by family

### Wi-Fi smart plugs (Tasmota)

1. On the plug's Tasmota console (its web UI → Console):
   `MqttHost <broker-ip>` · `Topic tasmota_plug01` · `TelePeriod 10`
2. Add a row to `config/tasmota_bridge.yaml` — device id, that topic, the model, `relay: true`:
   see [`../adapters/tasmota_bridge.yaml`](../adapters/tasmota_bridge.yaml) for the shape.
3. `docker compose restart tasmota-bridge`. The plug's cards light up; the next plug is steps 1–3
   again — a config row, no code.

### Zigbee (sensors, switches, plugs, DIN breakers, bulbs)

1. The ZB-GW04 dongle sits in the gateway box; Zigbee2MQTT runs as the `zigbee` profile service.
   In its web UI enable **permit join**, then put the device in pairing mode.
   **Aqara battery sensors:** hold the reset button ~5 s, pair *close to the coordinator*, and
   expect the first battery reading up to 24 h later.
2. Give it a friendly name in Z2M, then add a config row to `config/zigbee2mqtt_bridge.yaml`.
   Flags by device class: nothing for sensors; `relay: true` for anything switchable;
   `power_metering: true` if it measures mains power (plugs, the Tongou DIN breaker);
   `light: true` for bulbs.
3. **Turn permit join OFF when done.** For the Tongou DIN breaker also set its Z2M device options:
   `measurement_poll_interval: 10` and `power_outage_memory: restore`.
4. `docker compose restart z2m-bridge`.

### Modbus energy meters (Orno OR-WE-516)

1. Wire the meter's RS-485 A/B to the USB adapter (or an RS-485-to-Ethernet gateway for a
   multi-meter cabinet). Meter defaults: **ID 1, 9600 baud, 8E1**.
2. The `modbus` profile runs the adapter; set `METER_ID` and the serial device in the compose
   environment. Per-phase readings appear on channels `L1/L2/L3`, totals on `main`.
3. **First unit of a model on a bench:** verify readings against a reference meter and record the
   result in `catalog/` — that's part of the job, not an extra.

## 3. Verify the data (before touching the dashboard)

```bash
mosquitto_sub -h <broker> -t 'ms/<site>/#' -v        # canonical envelopes flowing?
```

Every device must show `$online true` and its readings topics. No data → check the bridge logs:
`docker compose logs -f z2m-bridge` (etc.).

## 4. Build the site's dashboard

1. Copy [`../dashboard/site.example.yaml`](../dashboard/site.example.yaml) → `<site>.yaml`; set
   `site`, `broker` (the `wss://…` URL in production), `api` (the historian URL), and the cards.
2. Optional branding: copy [`../dashboard/themes/example-client.yaml`](../dashboard/themes/example-client.yaml)
   and set the client's name/color/logo.
3. Compile — this **validates** every card against the schema and fails with a list of errors if
   you typo a metric or topic:

```bash
python tools/build_dashboard.py <site>.yaml --theme themes/<client>.yaml --out-dir <webroot>
```

4. Serve the `dashboard/` files + the built output from any static host. Hand the client the
   link; append `?role=viewer` for read-only links. Alert thresholds are card options
   (`alert: {below: 0.15}`) — set them at build time.

## 5. Handover checklist

- [ ] All devices `$online: true`; battery sensors have reported at least once
- [ ] Relay/switch round trip works from the dashboard (tap → device clicks → toggle confirms)
- [ ] History charts draw (historian has been ingesting for 15+ minutes)
- [ ] Client has installed the PWA, enabled the bell, and has viewer links for staff
- [ ] Permit join OFF; gateway survives a power cycle (pull the plug, watch it come back)
- [ ] New catalog entries/quirks from this site are committed back to the repo
