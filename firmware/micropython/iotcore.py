# iotcore for MicroPython — path C: your own firmware publishes canonical topics directly.
#
# Mirrors the Python SDK's contract on top of umqtt (micropython-lib):
#   - retained readings on ms/<site>/<device>/<channel>/<metric> in the canonical envelope,
#   - retained $meta descriptor + $online with a Last-Will that clears it on crash,
#   - per-point monotonic seq; RAM-bounded offline buffer replayed as q:stale on reconnect,
#   - /cmd subscription for writable metrics, state echoed from your handler's return value.
#
# See docs/firmware-esp32.md for the full guide. Works on ESP32 MicroPython and the unix port.
import json
import time

from iotcore_registry import METRICS, VERSION

_QUALITY = ("good", "stale", "estimated", "bad", "simulated")
# MicroPython epochs differ by port: ESP32 counts from 2000-01-01, unix from 1970.
_EPOCH_OFFSET_MS = 946684800000 if time.gmtime(0)[0] == 2000 else 0


def t_ms():
    return int(time.time() * 1000) + _EPOCH_OFFSET_MS


def build_envelope(metric, value, quality="good", source=None, medium=None, seq=None):
    if metric not in METRICS:
        raise ValueError("unknown metric " + metric)
    if quality not in _QUALITY:
        raise ValueError("invalid quality " + quality)
    env = {"t": t_ms(), "v": value, "u": METRICS[metric][0]}
    if quality != "good":
        env["q"] = quality
    if source is not None:
        env["src"] = source
    if medium is not None:
        env["medium"] = medium
    if seq is not None:
        env["seq"] = seq
    return env


class Device:
    """Wraps an (unconnected) umqtt MQTTClient with the canonical contract.

    points:   list of "metric" or ("metric", "channel")
    commands: list of writable metrics (same forms); on_command(metric, channel, value)
              is called for writes and its return value is echoed as the new state.
    """

    def __init__(self, client, site, device_id, model, points, commands=(),
                 source=None, on_command=None, buffer_max=500):
        self._c = client
        self.site = site
        self.device_id = device_id
        self.model = model
        self.source = source
        self.points = [self._norm(p) for p in points]
        self.commands = [self._norm(cmd) for cmd in commands]
        for metric, _ch in self.commands:
            if not METRICS[metric][1]:
                raise ValueError(metric + " is not writable in the registry")
        self.on_command = on_command
        self._base = "ms/%s/%s" % (site, device_id)
        self._seq = {}
        self._buf = []
        self._buffer_max = buffer_max
        self.connected = False

    @staticmethod
    def _norm(p):
        metric, ch = (p, "main") if isinstance(p, str) else (p[0], p[1])
        if metric not in METRICS:
            raise ValueError("unknown metric " + metric)
        return (metric, ch)

    def _topic(self, metric, channel):
        return "%s/%s/%s" % (self._base, channel, metric)

    def _descriptor(self):
        channels = {}
        for metric, ch in self.points + [p for p in self.commands if p not in self.points]:
            channels.setdefault(ch, [])
            if metric not in channels[ch]:
                channels[ch].append(metric)
        d = {"schema_version": VERSION, "device_id": self.device_id, "site": self.site,
             "model": self.model, "adapter": "fw-micropython@0.1", "online": True,
             "channels": [{"id": ch, "points": pts} for ch, pts in channels.items()]}
        if self.source is not None:
            d["source"] = self.source
        return d

    # ---- lifecycle ----
    def connect(self):
        self._c.set_last_will(self._base + "/$online", b"false", retain=True, qos=1)
        self._c.set_callback(self._cb)
        self._c.connect()
        self.connected = True
        self._c.publish(self._base + "/$meta", json.dumps(self._descriptor()),
                        retain=True, qos=1)
        self._c.publish(self._base + "/$online", b"true", retain=True, qos=1)
        for metric, ch in self.commands:
            self._c.subscribe(self._topic(metric, ch) + "/cmd", qos=1)
        self._replay()

    def check_cmd(self):
        """Call frequently (each loop tick) so /cmd writes are handled promptly."""
        try:
            self._c.check_msg()
        except OSError:
            self.connected = False
            raise

    def _cb(self, topic, payload):
        parts = topic.decode().split("/")
        if parts[-1] != "cmd" or self.on_command is None:
            return
        metric, channel = parts[-2], parts[-3]
        try:
            value = json.loads(payload)
        except ValueError:
            value = payload.decode()
        state = self.on_command(metric, channel, value)
        if state is not None:
            self.publish(metric, state, channel=channel)

    # ---- publishing ----
    def publish(self, metric, value, channel="main", source=None, quality="good"):
        key = (metric, channel)
        self._seq[key] = self._seq.get(key, 0) + 1
        env = build_envelope(metric, value, quality=quality,
                             source=source if source is not None else self.source,
                             seq=self._seq[key])
        topic = self._topic(metric, channel)
        if self.connected:
            try:
                self._c.publish(topic, json.dumps(env), retain=True, qos=1)
                return
            except OSError:
                self.connected = False
        if len(self._buf) < self._buffer_max:
            self._buf.append((topic, env))

    def _replay(self):
        while self._buf:
            topic, env = self._buf.pop(0)
            env.setdefault("q", "stale")
            self._c.publish(topic, json.dumps(env), retain=True, qos=1)

    def run(self, read, interval=5, on_tick=None):
        """Blocking loop: poll `read()` -> {metric | (metric, channel): value} every
        `interval` seconds, handle commands between polls, reconnect with backoff."""
        backoff = 1
        next_poll = 0
        while True:
            if not self.connected:
                try:
                    self.connect()
                    backoff = 1
                except OSError:
                    time.sleep(min(backoff, 60))
                    backoff *= 2
            try:
                if self.connected:
                    self.check_cmd()
                now = time.time()
                if now >= next_poll:
                    next_poll = now + interval
                    readings = read() or {}
                    for p, ch in self.points:
                        value = readings.get((p, ch), readings.get(p))
                        if value is not None:
                            self.publish(p, value, channel=ch)
                if on_tick:
                    on_tick()
            except OSError:
                self.connected = False
            time.sleep(0.2)
