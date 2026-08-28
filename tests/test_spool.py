"""Store-and-forward: offline readings spool to disk, survive a restart, and replay
in order marked q:stale. No broker needed."""
import json

from iotcore.adapter import Adapter


class FakeMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, json.loads(payload)))


def make_adapter(tmp_path, **kw):
    a = Adapter(site="s1", device_id="d1", spool_dir=str(tmp_path), **kw)
    a._connected = False
    return a


def test_offline_readings_spool_to_disk(tmp_path):
    a = make_adapter(tmp_path)
    for v in (230.1, 231.2, 229.9):
        a.publish("electrical.voltage", v)
    spool = tmp_path / "s1.d1.spool.jsonl"
    lines = [json.loads(line) for line in spool.read_text().splitlines()]
    assert [env["v"] for _, env in lines] == [230.1, 231.2, 229.9]
    assert [env["seq"] for _, env in lines] == [1, 2, 3]


def test_spool_survives_restart_and_replays_stale(tmp_path):
    a = make_adapter(tmp_path)
    a.publish("electrical.voltage", 230.1)
    a.publish("electrical.energy", 5000.0)

    # "restart": a brand-new process picks up the same spool dir
    b = make_adapter(tmp_path)
    b._client = FakeMqtt()
    b._replay_buffered()

    assert [env["v"] for _, env in b._client.published] == [230.1, 5000.0]
    assert all(env["q"] == "stale" for _, env in b._client.published)
    assert not (tmp_path / "s1.d1.spool.jsonl").exists()  # drained after replay


def test_replay_preserves_explicit_quality(tmp_path):
    a = make_adapter(tmp_path)
    a.publish("electrical.voltage", 230.1, quality="estimated")
    a._client = FakeMqtt()
    a._replay_buffered()
    assert a._client.published[0][1]["q"] == "estimated"  # stale only fills the default


def test_memory_buffer_still_replays_without_spool(tmp_path):
    a = Adapter(site="s1", device_id="d1")
    a._connected = False
    a.publish("electrical.voltage", 230.1)
    assert len(a._buffer) == 1
    a._client = FakeMqtt()
    a._replay_buffered()
    assert a._client.published[0][1]["q"] == "stale"
    assert not a._buffer
