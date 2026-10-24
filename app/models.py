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


class TrackView(BaseModel):
    id: str
    name: str
    role: Optional[str]
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
    name: str
    role: Optional[str]
    measurements: Measurements
    recommendations: List[Recommendation]


class MixAnalysisResponse(BaseModel):
    analyzed_at: datetime
    tracks: List[TrackFeedback]
    recommendations: List[Recommendation]
    excluded_stale_track_ids: List[str]


class HealthResponse(BaseModel):
    status: str
    registered_tracks: int
