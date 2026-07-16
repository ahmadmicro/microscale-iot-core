"""
Simulator adapters — emit canonical telemetry for a whole demo site, no hardware required.

Proves the contract end-to-end: sensor → canonical envelope → retained MQTT topic → dashboard.
Run this, open the dashboard, and you get live cards + a working relay toggle.

    python -m adapters.simulator            # site "acme-lagos" against localhost:1883

Three devices matching the two live engagements:
  main-meter  — 3-phase energy meter (voltage, power, energy), source flips grid⇄generator
  plug-01     — controllable smart plug (power + writable relay)   ← the control path
  tank-01     — diesel tank level (slowly draining)                ← Scope-1 fuel data
"""
from __future__ import annotations

import math
import sys
import threading
import time

# allow running from iot-core/ with the sdk on the path
sys.path.insert(0, "sdk")

from iotcore import Adapter, command, point  # noqa: E402

SITE = "acme-lagos"
HOST = "localhost"


def _t() -> float:
    return time.time()


class MainMeter(Adapter):
    model = "sim.energy-meter"
    poll_interval = 1.0
    accuracy_class = "1.0"
    points = [
        point("electrical.voltage"),
        point("electrical.current"),
        point("electrical.active_power"),
        point("electrical.power_factor"),
        point("electrical.frequency"),
        point("electrical.energy"),
    ]

    def __init__(self, **kw):
        super().__init__(**kw)
        self._energy_wh = 125_000.0  # cumulative import counter

    def read(self):
        now = _t()
        # load swings 1.5–4.5 kW on a slow sine + a little noise-free ripple
        power = 3000 + 1500 * math.sin(now / 20) + 300 * math.sin(now / 3)
        power = max(200.0, power)
        voltage = 230 + 8 * math.sin(now / 11)      # 222–238 V
        pf = 0.92 + 0.05 * math.sin(now / 7)
        current = power / (voltage * pf)
        self._energy_wh += power * (self.poll_interval / 3600.0)
        # source flips to generator for ~20 s each minute (simulates a grid outage)
        self.source = "generator" if int(now) % 60 < 20 else "grid"
        return {
            "electrical.voltage": round(voltage, 1),
            "electrical.current": round(current, 2),
            "electrical.active_power": round(power, 0),
            "electrical.power_factor": round(pf, 3),
            "electrical.frequency": round(50 + 0.1 * math.sin(now / 5), 2),
            "electrical.energy": round(self._energy_wh, 1),
        }


class SmartPlug(Adapter):
    model = "sim.smart-plug"
    poll_interval = 1.0
    source = "grid"
    points = [point("electrical.active_power"), point("device.relay")]
    commands = [command("device.relay")]

    def __init__(self, **kw):
        super().__init__(**kw)
        self._relay = "on"

    def read(self):
        draw = 1800 if self._relay == "on" else 0
        return {
            "electrical.active_power": draw + (50 if self._relay == "on" else 0),
            "device.relay": self._relay,
        }

    def on_command(self, metric, value):
        if metric == "device.relay":
            self._relay = "on" if value in ("on", 1, True, "1") else "off"
            print(f"[plug-01] relay -> {self._relay}")

    def on_command_state(self, metric, value):
        return self._relay


class DieselTank(Adapter):
    model = "sim.fuel-level"
    poll_interval = 2.0
    source = "generator"
    points = [point("fuel.level_fraction"), point("fuel.level")]

    def __init__(self, **kw):
        super().__init__(**kw)
        self._fraction = 0.62
        self._capacity_l = 1000.0

    def read(self):
        # drain faster while the meter says we're on generator; refuel jump if it hits empty
        self._fraction = max(0.05, self._fraction - 0.0008)
        if self._fraction <= 0.06:
            self._fraction = 0.9
        return {
            "fuel.level_fraction": round(self._fraction, 3),
            "fuel.level": round(self._fraction * self._capacity_l, 1),
        }


def main() -> None:
    devices = [
        MainMeter(site=SITE, device_id="main-meter", host=HOST),
        SmartPlug(site=SITE, device_id="plug-01", host=HOST),
        DieselTank(site=SITE, device_id="tank-01", host=HOST),
    ]
    threads = [threading.Thread(target=d.run, daemon=True) for d in devices]
    for th in threads:
        th.start()
    print(f"simulator running: site={SITE} devices={[d.device_id for d in devices]} (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping")


if __name__ == "__main__":
    main()
