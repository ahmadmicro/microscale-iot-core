# Building on the platform

*For developers. Assumes Python, MQTT basics, HTTP. Read
[device-integration.md](device-integration.md) first for the why; this is the how.*

## The contract (learn this once, everything else follows)

Every reading is a small JSON **envelope** on a retained, QoS-1 MQTT topic:

```
ms/<site>/<device>/<channel>/<metric>            ← readings (retained)
ms/<site>/<device>/<channel>/<metric>/cmd        ← writes to writable metrics
ms/<site>/<device>/$meta                         ← device descriptor (retained JSON)
ms/<site>/<device>/$online                       ← "true"/"false" (retained, Last-Will)
```

```json
{"t": 1787904000000, "v": 231.5, "u": "V", "q": "stale", "src": "grid", "seq": 42}
```

- `t` ms epoch at the source; `v` value in the metric's **canonical unit**; `u` optional echo of
  that unit; `q` quality (omitted = good; `stale` = replayed after an outage); `seq` per-point
  monotonic counter (gap detection, replay dedupe).
- Dimensions: `src` (grid/generator/solar/battery — carbon attribution on electrical readings)
  and `medium` (water/gas/steam/air — required on `flow.*` readings).
- The metric registry is [`../schema/canonical/metrics.yaml`](../schema/canonical/metrics.yaml) —
  **frozen at v1.0**. Adapters MUST convert to the canonical unit (registry rules at the top of
  the file). JSON Schemas for envelope and descriptor live beside it; CI validates against them.

## Writing a device adapter (path A — typed)

An adapter is ~20 lines on the SDK base; the base does MQTT, retained publishing, `$meta`,
Last-Will, seq, offline buffering, and command dispatch:

```python
from iotcore import Adapter, point, command

class MyMeter(Adapter):
    model = "vendor.model"          # links to catalog/<model>.yaml
    poll_interval = 5.0
    source = "grid"
    points = [point("electrical.active_power"), point("device.relay")]
    commands = [command("device.relay")]

    def read(self):                 # canonical units, keyed by metric
        return {"electrical.active_power": 1234.0, "device.relay": self._state}
        # multi-channel devices key by (metric, channel) instead

    def on_command(self, metric, value): ...     # writes arrive here
    def on_command_state(self, metric, value): return self._state

MyMeter(site="acme-lagos", device_id="meter-2", host="broker",
        spool_dir="/var/spool/iotcore").run()    # spool_dir = disk store-and-forward
```

For Modbus devices, subclass `iotcore.modbus.ModbusAdapter` and the *entire* adapter is register
declarations — see [`../adapters/orno_we516.py`](../adapters/orno_we516.py):

```python
points = [point("electrical.voltage", channel="L1", register=0x0E),
          point("electrical.energy", register=0x100, scale=1000)]   # kWh → W.h
```

## Bridges (path B — a family, zero code per device)

Whole families are already covered by config-driven bridges — a new Tasmota or Zigbee device is a
YAML row, not code: [`../adapters/tasmota_bridge.py`](../adapters/tasmota_bridge.py) and
[`../adapters/zigbee2mqtt_bridge.py`](../adapters/zigbee2mqtt_bridge.py) (flags: `relay`,
`power_metering`, `light`; the field maps at the top of each file are the translation contract).
Write a new bridge only for a new *family* — follow the same shape: map native topics → canonical
envelopes via `iotcore.schema.build_envelope`, never echo command state optimistically, republish
availability to `$online`.

## Device firmware (path C — no adapter at all)

Hardware you program yourself publishes the contract natively: the MicroPython port of the SDK
lives in [`../firmware/micropython/`](../firmware/micropython/) — see the
[ESP32 firmware guide](firmware-esp32.md), which doubles as the conformance checklist for a
future C/ESP-IDF implementation.

## Consuming data (the historian API)

Read-only HTTP on the historian (`:8090`; bearer token if `API_TOKEN` is set):

```
GET /{site}/devices                          registry ($meta + online)
GET /{site}/latest                           latest reading per point
GET /{site}/readings?metric=&device=&channel=&start=&end=&bucket=15 minutes
GET /{site}/energy?device=&start=&end=&bucket=1 hour
```

`readings` returns raw rows, or `avg/min/max/samples` per `time_bucket` when `bucket` is given.
`energy` is the carbon endpoint: per-`src` Wh with scope labels (grid → Scope 2, generator →
Scope 1), the meter's cumulative-counter delta as the audit total, and site `fuel.consumed`
litres. Method + caveats: [`../historian/README.md`](../historian/README.md). Prefer the API;
if you need SQL, the `readings` hypertable schema is
[`../historian/schema.sql`](../historian/schema.sql).

## Dashboards

Author YAML → `tools/build_dashboard.py` validates against the registry and compiles to the
runtime spec. Card types: `metric`, `gauge`, `source-split`, `switch`, `chart` (historian-backed).
See [`../dashboard/site.example.yaml`](../dashboard/site.example.yaml) for every option in use.

## Broker credentials while developing

Local dev Mosquitto is anonymous. Against production EMQX you need a JWT
([`../tools/mint_token.py`](../tools/mint_token.py)); adapters take it as MQTT username/password.

## Tests — run them

```bash
pip install -r adapters/requirements.txt -r historian/requirements.txt pytest jsonschema
cd deploy && docker compose up -d && cd ..     # broker for the integration suites
pytest tests/
```

Schema-contract and dashboard-build tests always run; adapter integration suites need the broker;
the EMQX-ACL and historian suites each need one extra container (the exact `docker run` is in the
test file's docstring — they skip cleanly otherwise). If you add a metric (minor version bump) or
an adapter, the suite is the definition of done — every existing adapter family ships with an
end-to-end test as the pattern to copy.
