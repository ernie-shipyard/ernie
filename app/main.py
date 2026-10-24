"""Local FastAPI service for ERNIE's plugin telemetry and mix feedback."""

import io
import os
from datetime import datetime, timezone
from typing import List
from urllib.parse import urlsplit

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.dsp import AudioDecodeError, analyze_audio
from app.feedback import build_recommendations
from app.models import (
    HealthResponse,
    MixAnalysisResponse,
    TrackFeedback,
    TrackListResponse,
    TrackRegistration,
    TrackRegistrationResponse,
    TrackView,
)
from app.registry import STALE_AFTER_SECONDS, TrackRegistry, TrackState


MAX_AUDIO_BYTES = 16 * 1024 * 1024
MAX_AUDIO_SECONDS = 30.0
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
app = FastAPI(
    title="ERNIE Local Mix Analysis API",
    version="0.1.0",
    description="Local plugin telemetry and evidence-based mix suggestions.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def _track_view(track: TrackState) -> TrackView:
    return TrackView(
        id=track.id,
        name=track.name,
        role=track.role,
        registered_at=track.registered_at,
        last_seen_at=track.last_seen_at,
        is_stale=track.is_stale(),
        latest_measurements=track.latest_measurements,
    )


async def _read_limited_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_AUDIO_BYTES:
                raise HTTPException(status_code=413, detail="WAV payload exceeds the 16 MiB limit.")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header.")
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="WAV payload exceeds the 16 MiB limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_wav(payload: bytes):
    try:
        audio, sample_rate = sf.read(io.BytesIO(payload), dtype="float32", always_2d=True)
    except (RuntimeError, ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail="Request body is not a readable WAV file.") from error
    if audio.ndim != 2 or audio.shape[1] not in (1, 2):
        raise HTTPException(status_code=422, detail="Only mono and stereo WAV audio is supported.")
    if audio.shape[0] == 0 or sample_rate <= 0:
        raise HTTPException(status_code=400, detail="WAV audio must contain samples and a valid sample rate.")
    if audio.shape[0] / float(sample_rate) > MAX_AUDIO_SECONDS:
        raise HTTPException(status_code=413, detail="WAV window exceeds the 30 second limit.")
    try:
        return analyze_audio(np.asarray(audio, dtype=np.float64), int(sample_rate))
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


@app.post("/tracks/{track_id}/audio", response_model=TrackView, tags=["tracks"])
async def upload_audio(track_id: str, request: Request) -> TrackView:
    if registry.get(track_id) is None:
        raise HTTPException(status_code=404, detail="Track is not registered.")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in ALLOWED_WAV_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Send raw WAV bytes with Content-Type: audio/wav.")
    payload = await _read_limited_body(request)
    if not payload:
        raise HTTPException(status_code=400, detail="WAV request body is empty.")
    measurements = _decode_wav(payload)
    track = registry.update_measurements(track_id, measurements)
    if track is None:
        raise HTTPException(status_code=404, detail="Track is not registered.")
    return _track_view(track)


@app.get("/tracks", response_model=TrackListResponse, tags=["tracks"])
def list_tracks() -> TrackListResponse:
    tracks = [_track_view(track) for track in registry.list_tracks()]
    return TrackListResponse(tracks=tracks, stale_after_seconds=STALE_AFTER_SECONDS)


@app.post("/mix/analyze", response_model=MixAnalysisResponse, tags=["mix"])
def analyze_mix() -> MixAnalysisResponse:
    all_tracks = registry.list_tracks()
    now = datetime.now(timezone.utc)
    active = [
        track for track in all_tracks
        if not track.is_stale() and track.latest_measurements is not None
    ]
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
        excluded_stale_track_ids=stale_ids,
    )
