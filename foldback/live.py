"""
Read (and lightly write) an Ableton Live session.

Backend: AbletonOSC  (https://github.com/ideoforms/AbletonOSC)
  - copy the AbletonOSC folder into  ~/Music/Ableton/User Library/Remote Scripts/
  - Live > Settings > Link/Tempo/MIDI > Control Surface: AbletonOSC
  - it listens on UDP 11000 and replies on 11001

If Live isn't running (or you're on the demo laptop), set FOLDBACK_MOCK=1 and
it reads session.mock.json instead, so every other feature still works.
"""

import json
import os
import threading
from dataclasses import dataclass, field, asdict
from dotenv import load_dotenv

load_dotenv()   # this module is imported before app.py gets to its own load_dotenv()
MOCK = os.getenv("FOLDBACK_MOCK") == "1"
MOCK_PATH = os.path.join(os.path.dirname(__file__), "session.mock.json")


@dataclass
class Track:
    index: int
    name: str
    volume: float = 0.85        # Live's 0..1 fader; 0.85 == 0 dB
    pan: float = 0.0            # -1..1
    mute: bool = False
    solo: bool = False
    arm: bool = False
    devices: list = field(default_factory=list)   # plugin / device names in order
    clips: list = field(default_factory=list)     # {"name":..., "length":..., "file_path":...}
    is_midi: bool = False
    sends: list = field(default_factory=list)


@dataclass
class Session:
    tempo: float
    tracks: list
    returns: list = field(default_factory=list)
    master_devices: list = field(default_factory=list)
    daw: str = "Ableton Live"
    key: str | None = None      # "A minor", "F# major"; None when the set has no key

    def to_dict(self):
        return asdict(self)


# --------------------------------------------------------------- OSC client

class AbletonOSC:
    def __init__(self, host="127.0.0.1", send_port=11000, recv_port=11001, timeout=1.5):
        from pythonosc.udp_client import SimpleUDPClient
        from pythonosc.dispatcher import Dispatcher
        from pythonosc.osc_server import ThreadingOSCUDPServer
        self.client = SimpleUDPClient(host, send_port)
        self.timeout = timeout
        self._waiting = {}
        self._batches = {}                  # addr -> {"want": set of arg prefixes, "got": {prefix: args}, "event"}
        self._lock = threading.Lock()
        self._query_lock = threading.Lock()  # replies are matched by address, so one query at a time
        disp = Dispatcher()
        disp.set_default_handler(self._on_msg)
        self.server = ThreadingOSCUDPServer((host, recv_port), disp)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _on_msg(self, addr, *args):
        with self._lock:
            ev = self._waiting.get(addr)
            batch = self._batches.get(addr)
            if batch is not None:
                for key in batch["want"]:
                    if tuple(args[:len(key)]) == key:
                        batch["got"][key] = args
                        if len(batch["got"]) == len(batch["want"]):
                            batch["event"].set()
                        break
        if ev:
            ev["args"] = args; ev["event"].set()

    def query(self, addr, *args):
        with self._query_lock:
            ev = {"event": threading.Event(), "args": None}
            with self._lock:
                self._waiting[addr] = ev
            self.client.send_message(addr, list(args))
            ok = ev["event"].wait(self.timeout)
            with self._lock:
                self._waiting.pop(addr, None)
            if not ok:
                raise TimeoutError(f"AbletonOSC didn't answer {addr} — is Live open with the control surface enabled?")
            return ev["args"]

    def query_many(self, addr, arg_lists, timeout=10.0):
        """Send every query at once and match replies by their leading args (e.g. track index).
        AbletonOSC drains its whole socket each Live tick, so N queries cost a few ticks instead of N."""
        keys = [tuple(a) for a in arg_lists]
        if not keys:
            return {}
        with self._query_lock:
            batch = {"want": set(keys), "got": {}, "event": threading.Event()}
            with self._lock:
                self._batches[addr] = batch
            for key in keys:
                self.client.send_message(addr, list(key))
            batch["event"].wait(timeout)
            with self._lock:
                self._batches.pop(addr, None)
            return batch["got"]      # missing keys simply aren't there

    def send(self, addr, *args):
        self.client.send_message(addr, list(args))


_osc = None
_osc_lock = threading.Lock()


def osc_client() -> AbletonOSC:
    """One client per process: each AbletonOSC() binds the reply port 11001, so a second one would fail to bind."""
    global _osc
    with _osc_lock:
        if _osc is None:
            _osc = AbletonOSC()
        return _osc


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR_SCALES = {"major", "ionian", "lydian", "mixolydian"}
MINOR_SCALES = {"minor", "aeolian", "dorian", "phrygian", "locrian", "harmonic minor", "melodic minor"}
_scale_mode_supported = True


def read_key(osc) -> str | None:
    """The set's key, or None when it has none. Live always reports a root and scale (C Major by default); Scale Mode
    says whether one is actually set. Stock AbletonOSC doesn't expose scale_mode (add it to song.py's properties_rw),
    and without it C Major is taken to mean unset."""
    global _scale_mode_supported
    try:
        root = int(osc.query("/live/song/get/root_note")[0]) % 12
        scale = str(osc.query("/live/song/get/scale_name")[0]).strip().lower()
        if _scale_mode_supported:
            try:
                if not osc.query("/live/song/get/scale_mode")[0]:
                    return None
            except TimeoutError:
                _scale_mode_supported = False
        if not _scale_mode_supported and (root, scale) == (0, "major"):
            return None
    except Exception:
        return None     # older Live / AbletonOSC: no key, the rest of the session still reads
    mode = "major" if scale in MAJOR_SCALES else "minor" if scale in MINOR_SCALES else ""
    return f"{NOTE_NAMES[root]} {mode}".strip()


def read_live_session() -> Session:
    """Bulk reads (AbletonOSC /live/song/get/track_data) plus pipelined per-track queries: a 50-track set reads in
    about a second instead of minutes of one-property-per-tick round trips."""
    osc = osc_client()
    tempo = osc.query("/live/song/get/tempo")[0]
    key = read_key(osc)
    n = int(osc.query("/live/song/get/num_tracks")[0])
    if n == 0:
        return Session(tempo=tempo, tracks=[], key=key)
    scenes = int(osc.query("/live/song/get/num_scenes")[0])

    FIELDS = ("track.name", "track.has_midi_input", "track.mute", "track.solo", "track.num_devices")
    flat = osc.query("/live/song/get/track_data", 0, n, *FIELDS)
    rows = [flat[i * len(FIELDS):(i + 1) * len(FIELDS)] for i in range(n)]

    dev_flat = list(osc.query("/live/song/get/track_data", 0, n, "device.name")) if any(r[4] for r in rows) else []
    devices, at = [], 0
    for r in rows:
        devices.append(dev_flat[at:at + int(r[4])]); at += int(r[4])

    clip_flat = list(osc.query("/live/song/get/track_data", 0, n, "clip.name")) if scenes else []
    clip_names = [clip_flat[i * scenes:(i + 1) * scenes] for i in range(n)]

    vols = osc.query_many("/live/track/get/volume", [(i,) for i in range(n)])
    pans = osc.query_many("/live/track/get/panning", [(i,) for i in range(n)])
    audio_clips = [(i, ci) for i in range(n) if not rows[i][1] for ci, c in enumerate(clip_names[i]) if c]
    paths = osc.query_many("/live/clip/get/file_path", audio_clips)

    tracks = []
    for i, (name, has_midi, mute, solo, _) in enumerate(rows):
        clips = [{"slot": ci, "name": c, "file_path": (paths.get((i, ci)) or (None, None, ""))[2] or ""}
                 for ci, c in enumerate(clip_names[i]) if c]
        tracks.append(Track(i, name, (vols.get((i,)) or (i, 0.85))[1], (pans.get((i,)) or (i, 0.0))[1],
                            bool(mute), bool(solo), False, devices[i], clips, bool(has_midi)))
    return Session(tempo=tempo, tracks=tracks, key=key)


def read_session() -> Session:
    if MOCK:
        d = json.load(open(MOCK_PATH))
        return Session(tempo=d["tempo"], tracks=[Track(**t) for t in d["tracks"]],
                       returns=d.get("returns", []), master_devices=d.get("master_devices", []), key=d.get("key"))
    return read_live_session()


# --------------------------------------------------------------- writes

NOTES_PER_MESSAGE = 64     # keeps each /live/clip/add/notes packet small


def create_midi_track(name="foldback", notes=None, length_beats=None):
    """New MIDI track at the end of the set. With notes ([(pitch, start_beat, dur_beats, velocity)]) it also gets a
    clip holding them in its first Session slot, ready to launch or copy into the Arrangement."""
    if MOCK: return {"mock": True, "track": name}
    osc = osc_client()
    osc.send("/live/song/create_midi_track", -1)
    i = osc.query("/live/song/get/num_tracks")[0] - 1
    osc.send("/live/track/set/name", i, name)
    if not notes:
        return {"track_index": i}
    length = float(length_beats or max(s + d for _, s, d, _ in notes))
    osc.send("/live/clip_slot/create_clip", i, 0, length)
    for at in range(0, len(notes), NOTES_PER_MESSAGE):
        flat = []
        for p, s, d, v in notes[at:at + NOTES_PER_MESSAGE]:
            flat += [int(p), float(s), float(d), int(v), False]
        osc.send("/live/clip/add/notes", i, 0, *flat)
    osc.send("/live/clip/set/name", i, 0, name)
    has_clip = bool(osc.query("/live/clip_slot/get/has_clip", i, 0)[-1])   # replies after the sends above
    return {"track_index": i, "clip_slot": 0 if has_clip else None}


def create_audio_track(name="foldback audio"):
    if MOCK: return {"mock": True, "track": name}
    osc = osc_client()
    osc.send("/live/song/create_audio_track", -1)
    n = osc.query("/live/song/get/num_tracks")[0]
    osc.send("/live/track/set/name", n - 1, name)
    return {"track_index": n - 1}


def set_tempo(bpm: float):
    if MOCK: return {"mock": True, "tempo": bpm}
    osc_client().send("/live/song/set/tempo", float(bpm))
    return {"tempo": bpm}
