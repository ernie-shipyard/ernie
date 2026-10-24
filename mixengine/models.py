from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TrackRegistration(BaseModel):
    instance_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=200)
    role: Optional[str] = Field(None, min_length=1, max_length=80)


class TrackRegistrationResponse(BaseModel):
    id: str
    name: str
    role: Optional[str]
    registered_at: datetime


class ListenRequest(BaseModel):
    minimum_context_seconds: float = Field(10.0, gt=0.0, le=600.0)
    max_duration_seconds: float = Field(300.0, gt=0.0, le=600.0)


class PluginListenRequest(ListenRequest):
    max_duration_seconds: float = Field(60.0, ge=0.25, le=120.0)
    plugin_port: int = Field(..., ge=17831, le=17862)
    playback_start_timeout_seconds: float = Field(120.0, gt=0.0, le=1800.0)


class CaptureCommand(BaseModel):
    capture_id: str
    track_id: str
    status: str
    requested_at: datetime
    minimum_context_seconds: float
    max_duration_seconds: float


class ListenPollResponse(BaseModel):
    capture: Optional[CaptureCommand]


class CaptureProgressRequest(BaseModel):
    status: str


class BandEnergy(BaseModel):
    low_hz: float
    high_hz: float
    energy_dbfs: Optional[float]


class Measurements(BaseModel):
    measured_at: datetime
    sample_rate: int
    channels: int
    duration_seconds: float
    is_silent: bool
    peak_dbfs: Optional[float]
    rms_dbfs: Optional[float]
    crest_factor_db: Optional[float]
    frequency_bands: Dict[str, BandEnergy]
    stereo_correlation: Optional[float]


class CaptureResponse(BaseModel):
    capture_id: str
    track_id: str
    status: str
    requested_at: datetime
    recording_started_at: Optional[datetime]
    completed_at: Optional[datetime]
    minimum_context_seconds: float
    max_duration_seconds: float
    duration_seconds: Optional[float]
    context_too_short: Optional[bool]
    measurements: Optional[Measurements] = None


class TrackView(BaseModel):
    id: str
    name: str
    role: Optional[str]
    latest_capture_id: Optional[str]
    registered_at: datetime
    last_seen_at: Optional[datetime]
    is_stale: bool
    latest_measurements: Optional[Measurements]


class TrackListResponse(BaseModel):
    tracks: List[TrackView]
    stale_after_seconds: float


class Recommendation(BaseModel):
    priority: str
    rank: int
    code: str
    track_id: str
    message: str
    reasoning: str
    evidence: Dict[str, Any]


class TrackFeedback(BaseModel):
    track_id: str
    capture_id: Optional[str]
    snapshot_is_stale: bool
    name: str
    role: Optional[str]
    measurements: Measurements
    recommendations: List[Recommendation]


class MixAnalysisResponse(BaseModel):
    analyzed_at: datetime
    tracks: List[TrackFeedback]
    recommendations: List[Recommendation]
    analyzed_capture_ids: List[str]
    excluded_stale_track_ids: List[str]
    excluded_short_context_capture_ids: List[str]


class MixAnalysisRequest(BaseModel):
    capture_ids: List[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    registered_tracks: int
