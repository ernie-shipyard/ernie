"""Local API for agent-requested, one-shot JUCE capture and mix analysis."""

import os
import json
import tempfile
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from urllib.parse import urlsplit

import soundfile as sf
from urllib.request import Request as UrlRequest, urlopen
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from mixengine.captures import CaptureRegistry, CaptureState
from mixengine.dsp import AudioDecodeError, analyze_wav_file
from mixengine.feedback import build_recommendations
from mixengine.models import (
    CaptureCommand,
    CaptureProgressRequest,
    CaptureResponse,
    HealthResponse,
    ListenPollResponse,
    ListenRequest,
    MixAnalysisRequest,
    MixAnalysisResponse,
    PluginListenRequest,
    TrackFeedback,
    TrackListResponse,
    TrackRegistration,
    TrackRegistrationResponse,
    TrackView,
)
from mixengine.registry import STALE_AFTER_SECONDS, TrackRegistry, TrackState


MAX_AUDIO_BYTES = 512 * 1024 * 1024
ALLOWED_WAV_CONTENT_TYPES = {"audio/wav", "audio/x-wav", "application/wav"}
DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def _cors_origins() -> List[str]:
    configured = os.getenv("ERNIE_CORS_ORIGINS")
    if configured is None:
        return DEFAULT_CORS_ORIGINS
    origins = []
    for origin in configured.split(","):
        origin = origin.strip()
        if not origin:
            continue
        parsed = urlsplit(origin)
        try:
            port = parsed.port
        except ValueError:
            continue
        if (
            parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            and port is not None
            and not parsed.username
            and not parsed.password
            and parsed.path == ""
            and parsed.query == ""
            and parsed.fragment == ""
        ):
            origins.append(origin)
    return origins


registry = TrackRegistry()
captures = CaptureRegistry()
_plugin_ports_lock = threading.Lock()
_plugin_ports_active = set()
app = FastAPI(
    title="ERNIE Local Mix Analysis API",
    version="0.2.0",
    description="Agent-requested JUCE capture and evidence-based mix suggestions.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Capture-ID"],
)


def _track_view(track: TrackState) -> TrackView:
    return TrackView(
        id=track.id,
        name=track.name,
        role=track.role,
        latest_capture_id=track.latest_capture_id,
        registered_at=track.registered_at,
        last_seen_at=track.last_seen_at,
        is_stale=track.is_stale(),
        latest_measurements=track.latest_measurements,
    )


def _capture_response(capture: CaptureState) -> CaptureResponse:
    return CaptureResponse(
        capture_id=capture.capture_id,
        track_id=capture.track_id,
        status=capture.status,
        requested_at=capture.requested_at,
        recording_started_at=capture.recording_started_at,
        completed_at=capture.completed_at,
        minimum_context_seconds=capture.minimum_context_seconds,
        max_duration_seconds=capture.max_duration_seconds,
        duration_seconds=capture.duration_seconds,
        context_too_short=capture.context_too_short,
        measurements=capture.measurements,
    )


def _capture_command(capture: CaptureState) -> CaptureCommand:
    return CaptureCommand(
        capture_id=capture.capture_id,
        track_id=capture.track_id,
        status=capture.status,
        requested_at=capture.requested_at,
        minimum_context_seconds=capture.minimum_context_seconds,
        max_duration_seconds=capture.max_duration_seconds,
    )


def _plugin_request(method: str, url: str, token: str = "", payload=None, timeout: float = 2.0) -> bytes:
    headers = {}
    data = None
    if token:
        headers["X-Ernie-Token"] = token
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = UrlRequest(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _plugin_capture_path(session_id: str) -> Path:
    """Resolve the exact WAV cache written by this JUCE session on this machine."""
    return (Path.home() / "Documents" / "ernie" / "captures"
            / "capture-{}.wav".format(session_id))


def _reserve_plugin_port(port: int) -> bool:
    with _plugin_ports_lock:
        if port in _plugin_ports_active:
            return False
        _plugin_ports_active.add(port)
        return True


def _release_plugin_port(port: int) -> None:
    with _plugin_ports_lock:
        _plugin_ports_active.discard(port)


async def _spool_limited_body(request: Request) -> str:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_AUDIO_BYTES:
                raise HTTPException(status_code=413, detail="WAV payload exceeds the 512 MiB limit.")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header.")

    path = None
    size = 0
    try:
        with tempfile.NamedTemporaryFile(prefix="ernie-capture-", suffix=".wav", delete=False) as cache_file:
            path = cache_file.name
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_AUDIO_BYTES:
                    raise HTTPException(status_code=413, detail="WAV payload exceeds the 512 MiB limit.")
                cache_file.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="WAV request body is empty.")
        return path
    except Exception:
        if path is not None and os.path.exists(path):
            os.unlink(path)
        raise


def _analyze_cached_wav(path: str, max_duration_seconds: float):
    try:
        info = sf.info(path)
    except (RuntimeError, ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail="Request body is not a readable WAV file.") from error
    if info.channels not in (1, 2):
        raise HTTPException(status_code=422, detail="Only mono and stereo WAV audio is supported.")
    if info.frames <= 0 or info.samplerate <= 0:
        raise HTTPException(status_code=400, detail="WAV audio must contain samples and a valid sample rate.")
    duration = info.frames / float(info.samplerate)
    if duration > max_duration_seconds:
        raise HTTPException(
            status_code=413,
            detail="WAV duration exceeds the requested {:.1f} second capture limit.".format(max_duration_seconds),
        )
    try:
        return analyze_wav_file(path)
    except AudioDecodeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/health", response_model=HealthResponse, tags=["integration"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", registered_tracks=registry.count())


@app.post(
    "/tracks/register",
    response_model=TrackRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["tracks"],
)
def register_track(body: TrackRegistration, response: Response) -> TrackRegistrationResponse:
    already_registered = registry.get(body.instance_id) is not None
    track = registry.register(body.instance_id, body.name, body.role)
    if already_registered:
        response.status_code = status.HTTP_200_OK
    return TrackRegistrationResponse(
        id=track.id,
        name=track.name,
        role=track.role,
        registered_at=track.registered_at,
    )


@app.post(
    "/tracks/{track_id}/listen",
    response_model=CaptureResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["capture"],
)
def request_listen(track_id: str, body: ListenRequest) -> CaptureResponse:
    if registry.get(track_id) is None:
        raise HTTPException(status_code=404, detail="Track is not registered.")
    if body.minimum_context_seconds > body.max_duration_seconds:
        raise HTTPException(status_code=422, detail="Minimum context cannot exceed the capture duration limit.")
    try:
        capture = captures.create(track_id, body.minimum_context_seconds, body.max_duration_seconds)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _capture_response(capture)


def _run_plugin_capture(track_id: str, capture_id: str, port: int, token: str,
                        playback_timeout: float, max_duration: float,
                        previous_session_id: str) -> None:
    """Poll one loopback JUCE instance, fetch its WAV, and run the normal DSP path."""
    base = "http://127.0.0.1:{}".format(port)
    deadline = time.monotonic() + playback_timeout + max_duration + 15.0
    try:
        while time.monotonic() < deadline:
            current = captures.get(capture_id)
            if current is None or current.status in {"cancelled", "failed"}:
                _plugin_request("POST", base + "/v1/recording/stop", token=token)
                return

            status_payload = json.loads(_plugin_request("GET", base + "/v1/status", timeout=2))
            is_new_session = bool(status_payload.get("session_id")) and (
                status_payload.get("session_id") != previous_session_id
            )
            if is_new_session and status_payload.get("recording"):
                current = captures.get(capture_id)
                if current is not None and current.status == "waiting_for_playback":
                    captures.update_plugin_status(capture_id, "recording")

            # capture_ready can still refer to a prior WAV while the new listen
            # is armed or recording. Accept only a WAV from the new session once
            # the plugin has stopped recording.
            if (is_new_session and not status_payload.get("recording")
                    and status_payload.get("capture_ready")
                    and status_payload.get("samples_written", 0) > 0):
                session_id = status_payload["session_id"]
                cached_path = _plugin_capture_path(session_id)
                if not cached_path.is_file():
                    time.sleep(0.25)
                    continue
                captures.begin_processing(capture_id)
                try:
                    measurements = _analyze_cached_wav(str(cached_path), max_duration)
                finally:
                    cached_path.unlink(missing_ok=True)
                captures.complete(capture_id, measurements)
                registry.update_measurements(track_id, measurements, capture_id=capture_id)
                return
            time.sleep(0.25)

        _plugin_request("POST", base + "/v1/recording/stop", token=token)
        captures.fail(capture_id)
    except Exception:
        captures.fail(capture_id)
    finally:
        _release_plugin_port(port)


@app.post("/tracks/{track_id}/plugin-listen", response_model=CaptureResponse,
          status_code=status.HTTP_202_ACCEPTED, tags=["capture"])
def request_plugin_listen(track_id: str, body: PluginListenRequest,
                          background_tasks: BackgroundTasks) -> CaptureResponse:
    """Arm a selected local JUCE instance; it waits for DAW playback and uploads its finished WAV internally."""
    if registry.get(track_id) is None:
        raise HTTPException(status_code=404, detail="Track is not registered.")
    if body.minimum_context_seconds > body.max_duration_seconds:
        raise HTTPException(status_code=422, detail="Minimum context cannot exceed the capture duration limit.")
    if body.max_duration_seconds > 120:
        raise HTTPException(status_code=422, detail="JUCE RAM captures are limited to 120 seconds per listen.")

    try:
        capture = captures.create(track_id, body.minimum_context_seconds, body.max_duration_seconds)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    if not _reserve_plugin_port(body.plugin_port):
        captures.fail(capture.capture_id)
        raise HTTPException(status_code=409, detail="A listen request is already active for that plugin instance.")

    base = "http://127.0.0.1:{}".format(body.plugin_port)
    try:
        handshake = json.loads(_plugin_request("POST", base + "/v1/handshake", timeout=2))
        token = handshake["token"]
        previous_session_id = json.loads(
            _plugin_request("GET", base + "/v1/status", timeout=2)
        ).get("session_id", "")
    except Exception as error:
        captures.fail(capture.capture_id)
        _release_plugin_port(body.plugin_port)
        raise HTTPException(status_code=502, detail="Could not handshake with that JUCE plugin instance.") from error

    try:
        _plugin_request(
            "POST", base + "/v1/listen", token=token,
            payload={"match_host_sample_rate": True,
                     "buffer_duration_seconds": body.max_duration_seconds},
            timeout=3,
        )
        captures.update_plugin_status(capture.capture_id, "waiting_for_playback")
    except Exception as error:
        captures.fail(capture.capture_id)
        _release_plugin_port(body.plugin_port)
        raise HTTPException(status_code=502, detail="JUCE plugin could not arm the requested capture.") from error

    background_tasks.add_task(
        _run_plugin_capture, track_id, capture.capture_id, body.plugin_port, token,
        body.playback_start_timeout_seconds, body.max_duration_seconds, previous_session_id,
    )
    return _capture_response(capture)


@app.get("/tracks/{track_id}/listen", response_model=ListenPollResponse, tags=["capture"])
def poll_listen(track_id: str) -> ListenPollResponse:
    if registry.get(track_id) is None:
        raise HTTPException(status_code=404, detail="Track is not registered.")
    capture = captures.active_for_track(track_id)
    return ListenPollResponse(capture=_capture_command(capture) if capture is not None else None)


@app.post("/captures/{capture_id}/status", response_model=CaptureResponse, tags=["capture"])
def update_capture_status(capture_id: str, body: CaptureProgressRequest) -> CaptureResponse:
    capture = captures.get(capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="Capture request was not found.")
    try:
        capture = captures.update_plugin_status(capture_id, body.status)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _capture_response(capture)


@app.get("/captures/{capture_id}", response_model=CaptureResponse, tags=["capture"])
def get_capture(capture_id: str) -> CaptureResponse:
    capture = captures.get(capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="Capture request was not found.")
    return _capture_response(capture)


@app.post("/captures/{capture_id}/cancel", response_model=CaptureResponse, tags=["capture"])
def cancel_capture(capture_id: str) -> CaptureResponse:
    try:
        capture = captures.cancel(capture_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if capture is None:
        raise HTTPException(status_code=404, detail="Capture request was not found.")
    return _capture_response(capture)


@app.post("/tracks/{track_id}/audio", response_model=TrackView, tags=["tracks"])
async def upload_capture_audio(
    track_id: str,
    request: Request,
    capture_id: str = Header(..., alias="X-Capture-ID"),
) -> TrackView:
    track = registry.get(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track is not registered.")
    capture = captures.get(capture_id)
    if capture is None or capture.track_id != track_id:
        raise HTTPException(status_code=404, detail="Listen request was not found for this track.")
    if capture.status not in {"requested", "waiting_for_playback", "recording"}:
        raise HTTPException(status_code=409, detail="This listen request is no longer accepting audio.")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in ALLOWED_WAV_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Send raw WAV bytes with Content-Type: audio/wav.")
    try:
        captures.begin_processing(capture_id)
        path = await _spool_limited_body(request)
        try:
            measurements = _analyze_cached_wav(path, capture.max_duration_seconds)
        finally:
            if os.path.exists(path):
                os.unlink(path)
        completed = captures.complete(capture_id, measurements)
    except Exception:
        captures.fail(capture_id)
        raise
    if completed is None:
        raise HTTPException(status_code=404, detail="Listen request was not found.")
    updated = registry.update_measurements(track_id, measurements, capture_id=capture_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Track is not registered.")
    return _track_view(updated)


@app.get("/tracks", response_model=TrackListResponse, tags=["tracks"])
def list_tracks() -> TrackListResponse:
    tracks = [_track_view(track) for track in registry.list_tracks()]
    return TrackListResponse(tracks=tracks, stale_after_seconds=STALE_AFTER_SECONDS)


@app.post("/mix/analyze", response_model=MixAnalysisResponse, tags=["mix"])
def analyze_mix(body: MixAnalysisRequest = MixAnalysisRequest()) -> MixAnalysisResponse:
    all_tracks = registry.list_tracks()
    now = datetime.now(timezone.utc)
    short_context_ids = []
    stale_ids = []
    snapshot_staleness = {}
    if body.capture_ids:
        if len(body.capture_ids) > 128:
            raise HTTPException(status_code=422, detail="At most 128 capture IDs may be analyzed at once.")
        if len(body.capture_ids) != len(set(body.capture_ids)):
            raise HTTPException(status_code=422, detail="capture_ids must not contain duplicates.")
        selected = []
        selected_track_ids = set()
        for capture_id in body.capture_ids:
            capture = captures.get(capture_id)
            if capture is None:
                raise HTTPException(status_code=404, detail="Capture {} was not found.".format(capture_id))
            if capture.context_too_short:
                short_context_ids.append(capture_id)
                continue
            if capture.status != "completed" or capture.measurements is None:
                raise HTTPException(status_code=409, detail="Capture {} is not complete.".format(capture_id))
            source_track = registry.get(capture.track_id)
            if source_track is None:
                raise HTTPException(status_code=404, detail="Track for capture {} was not found.".format(capture_id))
            if source_track.id in selected_track_ids:
                raise HTTPException(status_code=422, detail="Provide at most one capture per track.")
            selected_track_ids.add(source_track.id)
            snapshot_staleness[capture_id] = (
                capture.completed_at is None
                or (now - capture.completed_at).total_seconds() > STALE_AFTER_SECONDS
            )
            selected.append(replace(
                source_track,
                latest_capture_id=capture.capture_id,
                last_seen_at=capture.completed_at,
                latest_measurements=capture.measurements,
            ))
        active = selected
    else:
        fresh = [
            track for track in all_tracks
            if not track.is_stale() and track.latest_measurements is not None
        ]
        active = []
        for track in fresh:
            latest_capture = captures.get(track.latest_capture_id) if track.latest_capture_id else None
            if latest_capture is not None and latest_capture.context_too_short:
                short_context_ids.append(latest_capture.capture_id)
            else:
                active.append(track)
        stale_ids = [
            track.id for track in all_tracks
            if track.is_stale() or track.latest_measurements is None
        ]
    recommendations_by_track = build_recommendations(active)
    recommendations = [item for values in recommendations_by_track.values() for item in values]
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(key=lambda item: (priority_order.get(item.priority, 3), item.rank, item.track_id))
    for rank, item in enumerate(recommendations, start=1):
        item.rank = rank
    feedback = [
        TrackFeedback(
            track_id=track.id,
            capture_id=track.latest_capture_id,
            snapshot_is_stale=snapshot_staleness.get(track.latest_capture_id, track.is_stale()),
            name=track.name,
            role=track.role,
            measurements=track.latest_measurements,
            recommendations=recommendations_by_track.get(track.id, []),
        )
        for track in active
        if track.latest_measurements is not None
    ]
    return MixAnalysisResponse(
        analyzed_at=now,
        tracks=feedback,
        recommendations=recommendations,
        analyzed_capture_ids=[track.latest_capture_id for track in active if track.latest_capture_id],
        excluded_stale_track_ids=stale_ids,
        excluded_short_context_capture_ids=short_context_ids,
    )
