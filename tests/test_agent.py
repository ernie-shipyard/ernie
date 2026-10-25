import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app as foldback_app
from mixengine.main import captures, registry
from tests.test_api import wav_bytes


@pytest.fixture
def client():
    registry.clear()
    captures.clear()
    with TestClient(foldback_app.app) as test_client:
        yield test_client
    captures.clear()
    registry.clear()


def fake_plugin(monkeypatch, tmp_path, audio, sample_rate, port=17833):
    """One plugin instance on `port`; every other port refuses, like a closed loopback socket."""
    (tmp_path / "capture-take.wav").write_bytes(wav_bytes(audio, sample_rate))
    statuses = iter([
        {"session_id": "", "recording": False, "capture_ready": False},
        {"session_id": "take", "recording": True, "capture_ready": False},
        {"session_id": "take", "recording": False, "capture_ready": True, "samples_written": len(audio)},
    ])

    def fake_request(method, url, token="", payload=None, timeout=2.0):
        if not url.startswith("http://127.0.0.1:{}/".format(port)):
            raise ConnectionRefusedError(url)
        if url.endswith("/v1/handshake"):
            return json.dumps({"token": "t"}).encode()
        if url.endswith("/v1/listen"):
            return b'{"ok":true}'
        if url.endswith("/v1/status"):
            return json.dumps(next(statuses, {"session_id": "take", "recording": False, "capture_ready": True,
                                              "samples_written": len(audio), "api_running": True})).encode()
        raise AssertionError(url)

    monkeypatch.setattr("mixengine.main._plugin_request", fake_request)
    monkeypatch.setattr("mixengine.main._plugin_capture_path", lambda sid: tmp_path / "capture-{}.wav".format(sid))


def test_listen_registers_master_finds_plugin_and_reports_clean_verdict(client, monkeypatch, tmp_path):
    sample_rate = 16000
    t = np.arange(sample_rate * 3) / sample_rate
    # moderate level, real transients: nothing the rules should flag on a lone master
    audio = (0.2 * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 2 * t))).astype(np.float32)
    right = 0.2 * np.sin(2 * np.pi * 330 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 2 * t)) + 0.5 * audio
    fake_plugin(monkeypatch, tmp_path, np.stack([audio, right.astype(np.float32)], axis=1), sample_rate)
    # the first status probe belongs to discovery, so skip the scripted session sequence for it
    monkeypatch.setattr("foldback.agent.plugin_ports", lambda: [17833])
    # a sine is nothing like an EDM master; this test is about the listen wiring, not the reference comparison
    monkeypatch.setattr("mixengine.feedback.REFERENCE", None)

    started = client.post("/agent/listen", json={"max_seconds": 5})
    assert started.status_code == 202
    assert started.json()["track_id"] == "master"
    assert started.json()["plugin_port"] == 17833
    assert registry.get("master").role == "master"

    result = client.get("/agent/listen/{}".format(started.json()["capture_id"])).json()
    assert result["done"] and result["status"] == "completed"
    assert result["verdict"] == "clean"
    assert result["notes"] == []
    assert result["measurements"]["duration_seconds"] == pytest.approx(3.0)


def test_listen_reports_notes_for_a_crushed_kick(client, monkeypatch, tmp_path):
    sample_rate = 16000
    fake_plugin(monkeypatch, tmp_path, np.full(sample_rate * 3, 0.5, dtype=np.float32), sample_rate)
    monkeypatch.setattr("foldback.agent.plugin_ports", lambda: [17833])
    started = client.post("/agent/listen", json={"track": "Kick", "max_seconds": 5}).json()
    result = client.get("/agent/listen/{}".format(started["capture_id"])).json()
    assert result["verdict"] == "has_notes"
    assert result["notes"][0]["fix"]


def test_plugin_discovery_skips_closed_ports(monkeypatch, tmp_path):
    fake_plugin(monkeypatch, tmp_path, np.zeros(10, dtype=np.float32), 16000, port=17840)
    from foldback import agent
    assert agent.plugin_ports() == [17840]


def test_listen_without_plugin_says_so(client, monkeypatch):
    monkeypatch.setattr("foldback.agent.plugin_ports", lambda: [])
    response = client.post("/agent/listen", json={})
    assert response.status_code == 404
    assert "plugin" in response.json()["detail"]


def test_signed_url_needs_configuration(client, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_AGENT_ID", raising=False)
    response = client.get("/agent/signed-url")
    assert response.status_code == 503
