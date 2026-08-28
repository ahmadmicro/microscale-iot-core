"""
ModbusAdapter — declarative Modbus base (the shape sketched in the original Layer-2 design).

An adapter author declares registers → canonical metrics and is done:

    class OrnoWE516(ModbusAdapter):
        model = "orno.we-516"
        points = [
            point("electrical.voltage", channel="L1", register=0x0E),
            point("electrical.active_power", register=0x1C, scale=1000),   # kW -> W
            point("electrical.energy", register=0x100, scale=1000),        # kWh -> W.h
        ]

Per-point extras consumed here (via Point.extra):
    register  (required) holding-register address, zero-based
    type      "float32" (default, two big-endian words) | "uint32" | "uint16" | "int16"
    scale     multiplier applied to the decoded value to reach the canonical unit (default 1)

Transport is chosen at construction time: `modbus_tcp="host:port"` (RS-485-to-Ethernet
gateways, or a simulator) or `serial_port="/dev/ttyUSB0"` for direct RTU. Everything else
(envelopes, retained topics, $meta, buffering, seq) is inherited from Adapter.
"""
from __future__ import annotations

import struct
from typing import Any

from .adapter import Adapter


class ModbusAdapter(Adapter):
    # --- declared by subclasses (with sensible meter defaults) ---
    modbus_unit: int = 1
    baudrate: int = 9600
    parity: str = "E"
    stopbits: int = 1

    def __init__(self, *, modbus_tcp: str | None = None, serial_port: str | None = None,
                 modbus_unit: int | None = None, **kw: Any):
        super().__init__(**kw)
        if (modbus_tcp is None) == (serial_port is None):
            raise ValueError("pass exactly one of modbus_tcp='host:port' or serial_port")
        if modbus_unit is not None:
            self.modbus_unit = modbus_unit
        if modbus_tcp is not None:
            from pymodbus.client import ModbusTcpClient
            host, _, port = modbus_tcp.partition(":")
            self._mb = ModbusTcpClient(host, port=int(port or 502))
        else:
            from pymodbus.client import ModbusSerialClient
            self._mb = ModbusSerialClient(port=serial_port, baudrate=self.baudrate,
                                          parity=self.parity, stopbits=self.stopbits,
                                          bytesize=8)

    # ---- the declarative read loop ----
    def read(self) -> dict[Any, Any]:
        if not self._mb.connected and not self._mb.connect():
            return {}
        readings: dict[Any, Any] = {}
        for p in self.points:
            value = self._read_point(p.extra)
            if value is not None:
                readings[(p.metric, p.channel)] = value
        return readings

    def _read_point(self, extra: dict[str, Any]) -> float | int | None:
        address = extra["register"]
        vtype = extra.get("type", "float32")
        words = 2 if vtype in ("float32", "uint32") else 1
        rr = self._mb.read_holding_registers(address=address, count=words,
                                             slave=self.modbus_unit)
        if rr.isError():
            return None
        regs = rr.registers
        if vtype == "float32":
            raw: float | int = struct.unpack(">f", struct.pack(">HH", *regs))[0]
        elif vtype == "uint32":
            raw = (regs[0] << 16) | regs[1]
        elif vtype == "int16":
            raw = struct.unpack(">h", struct.pack(">H", regs[0]))[0]
        else:  # uint16
            raw = regs[0]
        return raw * extra.get("scale", 1)
