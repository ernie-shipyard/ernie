"""One-shot listen requests shared between the agent API and JUCE plugin."""

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from app.models import Measurements


ACTIVE_CAPTURE_STATES = {"requested", "waiting_for_playback", "recording", "processing"}
PLUGIN_CAPTURE_STATES = {"waiting_for_playback", "recording"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CaptureState:
    capture_id: str
    track_id: str
    requested_at: datetime
    minimum_context_seconds: float
    max_duration_seconds: float
    status: str = "requested"
    recording_started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    context_too_short: Optional[bool] = None
    measurements: Optional[Measurements] = None


class CaptureRegistry:
    def __init__(self) -> None:
        self._captures: Dict[str, CaptureState] = {}
        self._lock = threading.RLock()

    def create(
        self,
        track_id: str,
        minimum_context_seconds: float,
        max_duration_seconds: float,
    ) -> CaptureState:
        with self._lock:
            active = next(
                (capture for capture in self._captures.values()
                 if capture.track_id == track_id and capture.status in ACTIVE_CAPTURE_STATES),
                None,
            )
            if active is not None:
                raise ValueError("A listen request is already active for this track.")
            capture = CaptureState(
                capture_id=str(uuid.uuid4()),
                track_id=track_id,
                requested_at=utc_now(),
                minimum_context_seconds=minimum_context_seconds,
                max_duration_seconds=max_duration_seconds,
            )
            self._captures[capture.capture_id] = capture
            return capture

    def get(self, capture_id: str) -> Optional[CaptureState]:
        with self._lock:
            return self._captures.get(capture_id)

    def active_for_track(self, track_id: str) -> Optional[CaptureState]:
        with self._lock:
            active = [
                capture for capture in self._captures.values()
                if capture.track_id == track_id and capture.status in ACTIVE_CAPTURE_STATES
            ]
            return active[-1] if active else None

    def update_plugin_status(self, capture_id: str, new_status: str) -> Optional[CaptureState]:
        with self._lock:
            capture = self._captures.get(capture_id)
            if capture is None:
                return None
            if new_status not in PLUGIN_CAPTURE_STATES:
                raise ValueError("Plugin status must be waiting_for_playback or recording.")
            if capture.status not in {"requested", "waiting_for_playback", "recording"}:
                raise ValueError("Capture is no longer active.")
            if new_status == "recording" and capture.recording_started_at is None:
                capture.recording_started_at = utc_now()
            capture.status = new_status
            return capture

    def complete(self, capture_id: str, measurements: Measurements) -> Optional[CaptureState]:
        with self._lock:
            capture = self._captures.get(capture_id)
            if capture is None:
                return None
            if capture.status != "processing":
                raise ValueError("Capture is not being processed.")
            capture.measurements = measurements
            capture.duration_seconds = measurements.duration_seconds
            capture.context_too_short = measurements.duration_seconds < capture.minimum_context_seconds
            capture.completed_at = utc_now()
            capture.status = "context_too_short" if capture.context_too_short else "completed"
            return capture

    def begin_processing(self, capture_id: str) -> Optional[CaptureState]:
        with self._lock:
            capture = self._captures.get(capture_id)
            if capture is None:
                return None
            if capture.status not in {"requested", "waiting_for_playback", "recording"}:
                raise ValueError("Capture is no longer active.")
            capture.status = "processing"
            return capture

    def fail(self, capture_id: str) -> None:
        with self._lock:
            capture = self._captures.get(capture_id)
            if capture is not None and capture.status in ACTIVE_CAPTURE_STATES:
                capture.status = "failed"
                capture.completed_at = utc_now()

    def cancel(self, capture_id: str) -> Optional[CaptureState]:
        with self._lock:
            capture = self._captures.get(capture_id)
            if capture is None:
                return None
            if capture.status not in {"requested", "waiting_for_playback", "recording"}:
                raise ValueError("Capture is no longer active.")
            capture.status = "cancelled"
            capture.completed_at = utc_now()
            return capture

    def clear(self) -> None:
        with self._lock:
            self._captures.clear()
