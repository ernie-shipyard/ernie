"""
Routes the voice agent's client tools call. Thin wrappers over mixengine and the existing foldback routes,
so one spoken request is one HTTP call from the React app.

  GET  /agent/status                 backend, plugin and agent readiness for the UI header
  GET  /agent/signed-url             short-lived ElevenLabs conversation URL; the API key never reaches the browser
  POST /agent/listen                 register the track if needed, find the plugin, arm a transport-gated capture
  GET  /agent/listen/{capture_id}    phase while waiting/recording, then measurements + notes + verdict
  POST /agent/listen/{capture_id}/cancel
"""

import json
import os
import re
from typing import Optional

import requests
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from mixengine import main as mix
from mixengine.feedback import REFERENCE, tonal_balance

router = APIRouter(prefix="/agent", tags=["agent"])

PLUGIN_PORTS = range(17831, 17863)     # the plugin scans 32 ports from 17831 (source/RecordingApi.cpp)
ELEVEN_API = "https://api.elevenlabs.io/v1"

# What the UI shows and Ernie can say while a capture is in flight.
PHASES = {
    "requested": "Arming the plugin.",
    "waiting_for_playback": "Armed. Waiting for you to hit play.",
    "recording": "Recording.",
    "processing": "Analyzing.",
    "completed": "Done.",
    "context_too_short": "That take was too short to judge.",
    "failed": "The capture failed.",
    "cancelled": "Cancelled.",
}
DONE = {"completed", "context_too_short", "failed", "cancelled"}


def plugin_ports() -> list:
    """Ports with a live ERNIE plugin API. GET /v1/status needs no token, and a closed loopback port refuses instantly."""
    found = []
    for port in PLUGIN_PORTS:
        try:
            status = json.loads(mix._plugin_request("GET", f"http://127.0.0.1:{port}/v1/status", timeout=0.3))
        except Exception:
            continue
        if status.get("api_running", True):
            found.append(port)
    return found


def _track_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "master"


# ---------------------------------------------------------------- status / auth

@router.get("/status")
def status():
    key = os.getenv("ELEVENLABS_API_KEY", "")
    return {
        "backend": True,
        "plugin_ports": plugin_ports(),
        "has_key": bool(key) and key != "your_key_here",
        "agent_configured": bool(os.getenv("ELEVENLABS_AGENT_ID")),
    }


@router.get("/signed-url")
def signed_url():
    key, agent_id = os.getenv("ELEVENLABS_API_KEY", ""), os.getenv("ELEVENLABS_AGENT_ID", "")
    if not key or key == "your_key_here" or not agent_id:
        raise HTTPException(503, "Set ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID in .env to talk to Ernie.")
    try:
        r = requests.get(f"{ELEVEN_API}/convai/conversation/get-signed-url", params={"agent_id": agent_id},
                         headers={"xi-api-key": key}, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        detail = getattr(e.response, "text", "") if getattr(e, "response", None) is not None else str(e)
        raise HTTPException(502, f"ElevenLabs refused the signed URL: {detail[:300]}")
    return {"signed_url": r.json()["signed_url"]}


# ---------------------------------------------------------------- listen -> analyze

class AgentListen(BaseModel):
    track: str = "master"
    plugin_port: Optional[int] = Field(None, ge=17831, le=17862)
    max_seconds: float = Field(20.0, ge=2.0, le=120.0)
    playback_timeout_seconds: float = Field(60.0, gt=0.0, le=600.0)


@router.post("/listen", status_code=202)
def listen(body: AgentListen, background_tasks: BackgroundTasks):
    track_id = _track_id(body.track)
    if mix.registry.get(track_id) is None:
        name = "Master" if track_id == "master" else body.track.strip()
        mix.registry.register(track_id, name, track_id)

    port = body.plugin_port
    if port is None:
        ports = plugin_ports()
        if not ports:
            raise HTTPException(404, "I can't find the Ernie plugin. Drop it on your master bus and make sure the DAW is open.")
        port = ports[0]

    capture = mix.request_plugin_listen(track_id, mix.PluginListenRequest(
        plugin_port=port,
        minimum_context_seconds=min(2.0, body.max_seconds),
        max_duration_seconds=body.max_seconds,
        playback_start_timeout_seconds=body.playback_timeout_seconds,
    ), background_tasks)
    return {"capture_id": capture.capture_id, "track_id": track_id, "plugin_port": port,
            "status": capture.status, "phase": PHASES.get(capture.status, capture.status)}


def _summary(m) -> dict:
    """The few numbers worth saying out loud; the full measurement stays in /captures/{id}."""
    r = lambda v, n=1: None if v is None else round(v, n)
    out = {"duration_seconds": r(m.duration_seconds), "peak_dbfs": r(m.peak_dbfs), "rms_dbfs": r(m.rms_dbfs),
           "loudness_lufs": r(m.loudness_lufs), "crest_factor_db": r(m.crest_factor_db),
           "stereo_correlation": r(m.stereo_correlation, 2), "low_end_correlation": r(m.low_end_correlation, 2),
           "width_side_to_mid_db": r(m.side_to_mid_db), "is_silent": m.is_silent}
    balance = tonal_balance(m)
    if balance:
        # what Ernie can answer "is my low end too loud?" from: dB vs. the typical reference, and how unusual that is
        out["tonal_balance_vs_references"] = {
            n: {"range": b["range"], "vs_typical_db": b["vs_typical_db"], "verdict": b["verdict"]}
            for n, b in balance.items()}
        out["reference"] = "{} {} tracks".format(REFERENCE.get("source_tracks"), REFERENCE.get("genre"))
    return out


@router.get("/listen/{capture_id}")
def listen_status(capture_id: str):
    capture = mix.get_capture(capture_id)
    out = {"capture_id": capture_id, "status": capture.status, "phase": PHASES.get(capture.status, capture.status),
           "done": capture.status in DONE}
    if capture.status in DONE and capture.measurements is not None:
        notes = [{"priority": n.priority, "message": n.message, "why": n.reasoning, "fix": n.fix}
                 for n in capture.recommendations]
        out["measurements"] = _summary(capture.measurements)
        out["notes"] = notes
        if capture.measurements.is_silent:
            out["verdict"] = "silent"
        elif capture.status == "context_too_short":
            out["verdict"] = "too_short"
        else:
            # low-priority notes are comparisons and nitpicks; only medium/high ones mean "fix this"
            out["verdict"] = "has_notes" if any(n["priority"] in ("high", "medium") for n in notes) else "clean"
    return out


@router.post("/listen/{capture_id}/cancel")
def listen_cancel(capture_id: str):
    return {"status": mix.cancel_capture(capture_id).status}
