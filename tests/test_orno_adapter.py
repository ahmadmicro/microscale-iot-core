"""Integration: register-accurate simulated OR-WE-516 (Modbus TCP) -> adapter -> canonical
topics, per-phase channels included."""
import struct
import threading
import time

import pytest

pymodbus = pytest.importorskip("pymodbus")
from pymodbus.datastore import (ModbusSequentialDataBlock, ModbusServerContext,  # noqa: E402
                                ModbusSlaveContext)
from pymodbus.server import StartTcpServer  # noqa: E402

from adapters.orno_we516 import OrnoWE516  # noqa: E402
from conftest import wait_for  # noqa: E402

SITE = "t-orno"
BASE = f"ms/{SITE}/meter-t1"
PORT = 15025

VALUES = {
    0x0E: 231.5, 0x10: 229.8, 0x12: 233.1,          # V L1/L2/L3
    0x14: 50.02,                                     # Hz
    0x16: 12.4, 0x18: 8.1, 0x1A: 15.9,               # A L1/L2/L3
    0x1C: 7.85, 0x1E: 2.75, 0x20: 1.8, 0x22: 3.3,    # kW total/L1/L2/L3
    0x24: 1.2, 0x2C: 8.1, 0x34: 0.94,                # kvar / kVA / pf
    0x100: 1523.45,                                  # kWh
}


@pytest.fixture(scope="module")
def modbus_meter():
    regs = [0] * 300
    for addr, v in VALUES.items():
        regs[addr:addr + 2] = list(struct.unpack(">HH", struct.pack(">f", v)))
    # pymodbus 3.8 datastore serves block index addr+1 -> pad one leading word
    store = ModbusSlaveContext(hr=ModbusSequentialDataBlock(0, [0] + regs))
    ctx = ModbusServerContext(slaves=store, single=True)
    threading.Thread(target=lambda: StartTcpServer(context=ctx, address=("localhost", PORT)),
                     daemon=True).start()
    time.sleep(0.8)
    return PORT


def test_meter_end_to_end(broker, observer, modbus_meter):
    obs = observer(f"ms/{SITE}/#")
    OrnoWE516.poll_interval = 1.0
    meter = OrnoWE516(site=SITE, device_id="meter-t1", modbus_tcp=f"localhost:{modbus_meter}")
    threading.Thread(target=meter.run, daemon=True).start()

    assert wait_for(lambda: obs.env(f"{BASE}/main/electrical.energy"), timeout=10)
    approx = pytest.approx
    assert obs.env(f"{BASE}/L1/electrical.voltage")["v"] == approx(231.5, abs=0.01)
    assert obs.env(f"{BASE}/L3/electrical.voltage")["v"] == approx(233.1, abs=0.01)
    assert obs.env(f"{BASE}/main/electrical.frequency")["v"] == approx(50.02, abs=0.01)
    assert obs.env(f"{BASE}/L2/electrical.current")["v"] == approx(8.1, abs=0.01)
    assert obs.env(f"{BASE}/main/electrical.active_power")["v"] == approx(7850, abs=1)  # kW -> W
    assert obs.env(f"{BASE}/L1/electrical.active_power")["v"] == approx(2750, abs=1)
    assert obs.env(f"{BASE}/main/electrical.reactive_power")["v"] == approx(1200, abs=1)
    assert obs.env(f"{BASE}/main/electrical.apparent_power")["v"] == approx(8100, abs=1)
    assert obs.env(f"{BASE}/main/electrical.power_factor")["v"] == approx(0.94, abs=0.001)
    energy = obs.env(f"{BASE}/main/electrical.energy")
    assert energy["v"] == approx(1523450, abs=100) and energy["src"] == "grid"  # kWh -> W.h
    meta = obs.env(f"{BASE}/$meta")
    assert {c["id"] for c in meta["channels"]} == {"main", "L1", "L2", "L3"}
    assert obs.seen.get(f"{BASE}/$online") == "true"
    meter._client.disconnect()
