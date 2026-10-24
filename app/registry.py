"""Small in-memory registry for local plugin instances."""

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.models import Measurements


STALE_AFTER_SECONDS = 10.0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TrackState:
    id: str
    name: str
    role: Optional[str]
    registered_at: datetime
    last_seen_at: Optional[datetime] = None
    last_seen_monotonic: Optional[float] = None
    latest_measurements: Optional[Measurements] = None

    def is_stale(self, now_monotonic: Optional[float] = None) -> bool:
        if self.last_seen_monotonic is None:
            return True
        now = time.monotonic() if now_monotonic is None else now_monotonic
        return now - self.last_seen_monotonic > STALE_AFTER_SECONDS


class TrackRegistry:
    def __init__(self) -> None:
        self._tracks: Dict[str, TrackState] = {}
        self._lock = threading.RLock()

    def register(self, instance_id: str, name: str, role: Optional[str]) -> TrackState:
        with self._lock:
            existing = self._tracks.get(instance_id)
            if existing is not None:
                existing.name = name
                existing.role = role
                return existing
            track = TrackState(
                id=instance_id,
                name=name,
                role=role,
                registered_at=utc_now(),
            )
            self._tracks[instance_id] = track
            return track

    def get(self, track_id: str) -> Optional[TrackState]:
        with self._lock:
            return self._tracks.get(track_id)

    def update_measurements(self, track_id: str, measurements: Measurements) -> Optional[TrackState]:
        with self._lock:
            track = self._tracks.get(track_id)
            if track is None:
                return None
            track.latest_measurements = measurements
            track.last_seen_at = utc_now()
            track.last_seen_monotonic = time.monotonic()
            return track

    def list_tracks(self) -> List[TrackState]:
        with self._lock:
            return list(self._tracks.values())

    def count(self) -> int:
        with self._lock:
            return len(self._tracks)

    def clear(self) -> None:
        with self._lock:
            self._tracks.clear()
