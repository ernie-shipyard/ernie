import io
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from mixengine.main import app, captures, registry


@pytest.fixture
def client():
    registry.clear()
    captures.clear()
    with TestClient(app) as test_client:
        yield test_client
    captures.clear()
    registry.clear()


def wav_bytes(audio, sample_rate):
    payload = io.BytesIO()
    sf.write(payload, audio, sample_rate, format="WAV", subtype="FLOAT")
    return payload.getvalue()


def register(client, instance_id="track-a", name="Track A", role=None):
    body = {"instance_id": instance_id, "name": name}
    if role is not None:
        body["role"] = role
    return client.post("/tracks/register", json=body)


def upload(client, track_id, audio, sample_rate):
    listen = client.post(
        "/tracks/{}/listen".format(track_id),
        json={"minimum_context_seconds": 0.1, "max_duration_seconds": 300},
    )
    assert listen.status_code == 202
    capture_id = listen.json()["capture_id"]
    client.get("/tracks/{}/listen".format(track_id))
    client.post("/captures/{}/status".format(capture_id), json={"status": "waiting_for_playback"})
    client.post("/captures/{}/status".format(capture_id), json={"status": "recording"})
    return client.post(
        "/tracks/{}/audio".format(track_id),
        content=wav_bytes(audio, sample_rate),
        headers={"Content-Type": "audio/wav", "X-Capture-ID": capture_id},
    )


def test_health_and_idempotent_registration(client):
    assert client.get("/health").json() == {"status": "ok", "registered_tracks": 0}
    first = register(client)
    second = register(client, name="Renamed Track")
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == "track-a"
    assert second.json()["name"] == "Renamed Track"
    assert client.get("/health").json()["registered_tracks"] == 1


def test_agent_listen_request_is_polled_and_returns_capture_status(client):
    register(client)
    response = client.post(
        "/tracks/track-a/listen",
        json={"minimum_context_seconds": 2.0, "max_duration_seconds": 30.0},
    )
    assert response.status_code == 202
    capture_id = response.json()["capture_id"]
    command = client.get("/tracks/track-a/listen").json()["capture"]
    assert command["capture_id"] == capture_id
    assert command["status"] == "requested"
    assert client.post(
        "/tracks/track-a/listen", json={"minimum_context_seconds": 1.0}
    ).status_code == 409
    assert client.post(
        "/captures/{}/status".format(capture_id), json={"status": "waiting_for_playback"}
    ).json()["status"] == "waiting_for_playback"
    assert client.post("/captures/{}/cancel".format(capture_id)).json()["status"] == "cancelled"
    assert client.get("/tracks/track-a/listen").json()["capture"] is None


def test_plugin_listen_orchestrates_juce_capture_and_analysis(client, monkeypatch, tmp_path):
    register(client)
    sample_rate = 16000
    time = np.arange(sample_rate // 2) / sample_rate
    payload = wav_bytes(0.25 * np.sin(2 * np.pi * 440 * time), sample_rate)
    statuses = iter([
        {"recording": True, "capture_ready": False},
        {"recording": False, "capture_ready": True},
    ])

    def fake_plugin_request(method, url, token="", payload=None, timeout=2.0):
        if url.endswith("handshake"):
            return json.dumps({"token": "local-test-token"}).encode()
        if url.endswith("/v1/listen"):
            assert method == "POST"
            assert token == "local-test-token"
            assert payload["buffer_duration_seconds"] == 2.0
            return b'{"ok":true}'
        if url.endswith("/v1/status"):
            return json.dumps(next(statuses)).encode()
        raise AssertionError("unexpected plugin request: {}".format(url))

    def fake_download(url, token, timeout=30.0):
        assert url.endswith("/v1/capture.wav")
        assert token == "local-test-token"
        path = tmp_path / "capture.wav"
        path.write_bytes(payload)
        return str(path)

    monkeypatch.setattr("mixengine.main._plugin_request", fake_plugin_request)
    monkeypatch.setattr("mixengine.main._download_plugin_wav", fake_download)
    response = client.post(
        "/tracks/track-a/plugin-listen",
        json={"plugin_port": 17841, "minimum_context_seconds": 0.1,
              "max_duration_seconds": 2.0, "playback_start_timeout_seconds": 1.0},
    )
    assert response.status_code == 202
    capture_id = response.json()["capture_id"]
    completed = client.get("/captures/{}".format(capture_id)).json()
    assert completed["status"] == "completed"
    assert completed["measurements"]["channels"] == 1
    assert registry.get("track-a").latest_capture_id == capture_id


def test_mono_wav_measurements_and_list_contract(client):
    register(client)
    sample_rate = 44100
    time = np.arange(sample_rate // 2) / sample_rate
    audio = 0.5 * np.sin(2 * np.pi * 1000 * time)
    response = upload(client, "track-a", audio, sample_rate)
    assert response.status_code == 200
    measurements = response.json()["latest_measurements"]
    assert measurements["sample_rate"] == sample_rate
    assert measurements["channels"] == 1
    assert measurements["duration_seconds"] == pytest.approx(0.5)
    assert measurements["peak_dbfs"] == pytest.approx(-6.0206, abs=0.02)
    assert measurements["rms_dbfs"] == pytest.approx(-9.0309, abs=0.03)
    assert measurements["stereo_correlation"] is None
    assert measurements["frequency_bands"]["mid"]["energy_dbfs"] is not None
    listed = client.get("/tracks").json()["tracks"][0]
    assert listed["is_stale"] is False
    assert listed["latest_measurements"]["sample_rate"] == sample_rate
    assert listed["latest_capture_id"] is not None


def test_stereo_correlation_and_role_level_comparison(client):
    register(client, "guitar-1", "Guitar 1", role="guitar")
    register(client, "guitar-2", "Guitar 2", role="guitar")
    register(client, "guitar-3", "Guitar 3", role="guitar")
    sample_rate = 22050
    time = np.arange(sample_rate // 4) / sample_rate
    base = 0.5 * np.sin(2 * np.pi * 330 * time)
    response = upload(client, "guitar-1", np.column_stack((base, -base)), sample_rate)
    assert response.status_code == 200
    measurements = response.json()["latest_measurements"]
    assert measurements["channels"] == 2
    assert measurements["stereo_correlation"] == pytest.approx(-1.0, abs=1e-6)
    assert measurements["frequency_bands"]["low_mid"]["energy_dbfs"] is not None

    upload(client, "guitar-2", np.column_stack((base, base)), sample_rate)
    louder = np.sin(2 * np.pi * 330 * time)
    upload(client, "guitar-3", np.column_stack((louder, louder)), sample_rate)
    analysis = client.post("/mix/analyze").json()
    assert any(item["code"] == "negative_stereo_correlation" for item in analysis["recommendations"])
    assert any(item["code"] == "same_role_level_outlier" for item in analysis["recommendations"])
    assert [item["rank"] for item in analysis["recommendations"]] == list(
        range(1, len(analysis["recommendations"]) + 1)
    )


def test_silence_has_null_levels_and_no_recommendations(client):
    register(client)
    audio = np.zeros(2048, dtype=np.float32)
    response = upload(client, "track-a", audio, 48000)
    measurements = response.json()["latest_measurements"]
    assert measurements["is_silent"] is True
    assert measurements["peak_dbfs"] is None
    assert measurements["rms_dbfs"] is None
    assert measurements["crest_factor_db"] is None
    assert client.post("/mix/analyze").json()["recommendations"] == []


def test_high_band_energy_alone_does_not_flag_eq_problem(client):
    register(client)
    sample_rate = 44100
    time = np.arange(sample_rate // 2) / sample_rate
    audio = 0.25 * np.sin(2 * np.pi * 9000 * time)
    upload(client, "track-a", audio, sample_rate)
    analysis = client.post("/mix/analyze").json()
    assert analysis["recommendations"] == []
    high = analysis["tracks"][0]["measurements"]["frequency_bands"]["high"]["energy_dbfs"]
    assert high is not None


def test_stale_tracks_are_listed_but_excluded_from_mix(client, monkeypatch):
    register(client)
    sample_rate = 16000
    time = np.arange(1024) / sample_rate
    upload(client, "track-a", 0.2 * np.sin(2 * np.pi * 440 * time), sample_rate)
    track = registry.get("track-a")
    monkeypatch.setattr("mixengine.registry.time.monotonic", lambda: track.last_seen_monotonic + 11)
    listed = client.get("/tracks").json()["tracks"][0]
    assert listed["is_stale"] is True
    assert listed["latest_measurements"] is not None
    analysis = client.post("/mix/analyze").json()
    assert analysis["tracks"] == []
    assert analysis["excluded_stale_track_ids"] == ["track-a"]


def test_agent_can_analyze_an_explicit_older_capture_without_calling_it_current(client, monkeypatch):
    register(client)
    sample_rate = 16000
    time = np.arange(4096) / sample_rate
    upload(client, "track-a", 0.2 * np.sin(2 * np.pi * 440 * time), sample_rate)
    capture_id = registry.get("track-a").latest_capture_id
    track = registry.get("track-a")
    captures.get(capture_id).completed_at = datetime.now(timezone.utc) - timedelta(seconds=11)
    monkeypatch.setattr("mixengine.registry.time.monotonic", lambda: track.last_seen_monotonic + 11)
    result = client.post("/mix/analyze", json={"capture_ids": [capture_id]}).json()
    assert result["tracks"][0]["capture_id"] == capture_id
    assert result["tracks"][0]["snapshot_is_stale"] is True
    assert result["analyzed_capture_ids"] == [capture_id]
    assert result["excluded_stale_track_ids"] == []


def test_short_capture_is_reported_and_excluded_from_mix_feedback(client):
    register(client)
    listen = client.post(
        "/tracks/track-a/listen",
        json={"minimum_context_seconds": 2.0, "max_duration_seconds": 30.0},
    ).json()
    capture_id = listen["capture_id"]
    sample_rate = 16000
    time = np.arange(1024) / sample_rate
    audio = 0.2 * np.sin(2 * np.pi * 440 * time)
    response = client.post(
        "/tracks/track-a/audio",
        content=wav_bytes(audio, sample_rate),
        headers={"Content-Type": "audio/wav", "X-Capture-ID": capture_id},
    )
    assert response.status_code == 200
    capture = client.get("/captures/{}".format(capture_id)).json()
    assert capture["status"] == "context_too_short"
    assert capture["context_too_short"] is True
    assert capture["duration_seconds"] == pytest.approx(1024 / sample_rate)
    analysis = client.post("/mix/analyze").json()
    assert analysis["tracks"] == []
    assert analysis["excluded_short_context_capture_ids"] == [capture_id]


def test_invalid_audio_requests_return_client_errors(client):
    assert client.post(
        "/tracks/missing/audio",
        content=b"x",
        headers={"Content-Type": "audio/wav", "X-Capture-ID": "missing"},
    ).status_code == 404
    register(client)
    capture_id = client.post("/tracks/track-a/listen", json={}).json()["capture_id"]
    bad_wav = client.post(
        "/tracks/track-a/audio",
        content=b"not a wav",
        headers={"Content-Type": "audio/wav", "X-Capture-ID": capture_id},
    )
    assert bad_wav.status_code == 400
    wrong_type = client.post(
        "/tracks/track-a/audio",
        content=b"x",
        headers={"Content-Type": "application/octet-stream", "X-Capture-ID": client.post(
            "/tracks/track-a/listen", json={}
        ).json()["capture_id"]},
    )
    assert wrong_type.status_code == 415
