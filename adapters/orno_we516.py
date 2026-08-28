"""
Orno OR-WE-516 — 3-phase DIN-rail energy meter, Modbus RTU (the SBE sub-metering path).

The whole adapter is the register declaration below; ModbusAdapter does the rest.
Register map verified against the OR-WE-516/517 community documentation (ESPHome device
page, Loxone library) — FP32 holding registers, FC03. Meter serial defaults (from the
Orno manual): ID 1, 9600 baud, 8E1.

    python -m adapters.orno_we516 --serial /dev/ttyUSB0 --site acme-lagos --device meter-01
    python -m adapters.orno_we516 --tcp 192.168.1.50:502 ...   # RS-485-to-Ethernet gateway

Per-phase readings publish on channels L1/L2/L3; totals on channel main.
"""
from __future__ import annotations

import argparse
import sys

# allow running from the repo root with the sdk on the path
sys.path.insert(0, "sdk")

from iotcore import point  # noqa: E402
from iotcore.modbus import ModbusAdapter  # noqa: E402


class OrnoWE516(ModbusAdapter):
    model = "orno.we-516"
    poll_interval = 5.0            # meets the <=5 s refresh gate
    source = "grid"
    accuracy_class = "B"           # nameplate; measured accuracy goes in the catalog

    points = [
        # voltages (V, FP32)
        point("electrical.voltage", channel="L1", register=0x0E),
        point("electrical.voltage", channel="L2", register=0x10),
        point("electrical.voltage", channel="L3", register=0x12),
        point("electrical.frequency", register=0x14),
        # currents (A, FP32)
        point("electrical.current", channel="L1", register=0x16),
        point("electrical.current", channel="L2", register=0x18),
        point("electrical.current", channel="L3", register=0x1A),
        # active power (meter reports kW -> canonical W)
        point("electrical.active_power", register=0x1C, scale=1000),
        point("electrical.active_power", channel="L1", register=0x1E, scale=1000),
        point("electrical.active_power", channel="L2", register=0x20, scale=1000),
        point("electrical.active_power", channel="L3", register=0x22, scale=1000),
        # totals: reactive (kvar -> var), apparent (kVA -> V.A), power factor
        point("electrical.reactive_power", register=0x24, scale=1000),
        point("electrical.apparent_power", register=0x2C, scale=1000),
        point("electrical.power_factor", register=0x34),
        # total active energy (kWh -> W.h)
        point("electrical.energy", register=0x100, scale=1000),
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description="Orno OR-WE-516 Modbus adapter")
    ap.add_argument("--site", required=True)
    ap.add_argument("--device", required=True)
    ap.add_argument("--host", default="localhost", help="MQTT broker host")
    ap.add_argument("--tcp", help="Modbus TCP endpoint host:port (RS-485 gateway)")
    ap.add_argument("--serial", help="serial device for direct RTU, e.g. /dev/ttyUSB0")
    ap.add_argument("--unit", type=int, default=1, help="Modbus slave id (meter default 1)")
    args = ap.parse_args()

    OrnoWE516(site=args.site, device_id=args.device, host=args.host,
              modbus_tcp=args.tcp, serial_port=args.serial,
              modbus_unit=args.unit).run()


if __name__ == "__main__":
    main()
