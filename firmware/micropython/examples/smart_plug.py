# Example ESP32 firmware: a metering smart plug speaking the canonical contract natively
# (path C — no adapter, no bridge; the device IS a first-class citizen of the platform).
#
# Copy to the board as main.py alongside iotcore.py + iotcore_registry.py, edit CONFIG.
import json
import time

import network
import ntptime
from machine import Pin
from umqtt.robust import MQTTClient

from iotcore import Device

CONFIG = {
    "wifi": ("<ssid>", "<psk>"),
    "broker": "192.168.1.10",           # EMQX/Mosquitto host
    "token": None,                      # EMQX device JWT (tools/mint_token.py); None for dev broker
    "site": "acme-lagos",
    "device_id": "plug-fw-01",
}

RELAY = Pin(2, Pin.OUT, value=1)


def wifi_up():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(*CONFIG["wifi"])
        while not wlan.isconnected():
            time.sleep(0.5)
    ntptime.settime()                   # envelope `t` must be real epoch time
    return wlan


def read_power():
    # replace with your metering IC (HLW8012, INA226, ...) — canonical unit is WATTS
    return 1800.0 if RELAY.value() else 0.0


def on_command(metric, channel, value):
    if metric == "device.relay":
        RELAY.value(1 if value in ("on", 1, True, "1") else 0)
        return "on" if RELAY.value() else "off"


wlan = wifi_up()
client = MQTTClient("%s.%s" % (CONFIG["site"], CONFIG["device_id"]), CONFIG["broker"],
                    user=CONFIG["device_id"] if CONFIG["token"] else None,
                    password=CONFIG["token"], keepalive=30)

device = Device(client, site=CONFIG["site"], device_id=CONFIG["device_id"],
                model="microscale.plug-fw", source="grid",
                points=["electrical.active_power", "device.relay", "device.rssi"],
                commands=["device.relay"], on_command=on_command)


def read():
    return {
        "electrical.active_power": read_power(),
        "device.relay": "on" if RELAY.value() else "off",
        "device.rssi": wlan.status("rssi"),
    }


device.run(read, interval=5)
