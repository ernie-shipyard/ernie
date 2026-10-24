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
        self._lock = threading.Lock()
        disp = Dispatcher()
        disp.set_default_handler(self._on_msg)
        self.server = ThreadingOSCUDPServer((host, recv_port), disp)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _on_msg(self, addr, *args):
        with self._lock:
            ev = self._waiting.get(addr)
        if ev:
            ev["args"] = args; ev["event"].set()

    def query(self, addr, *args):
        ev = {"event": threading.Event(), "args": None}
        with self._lock:
            self._waiting[addr] = ev
        self.client.send_message(addr, list(args))
        if not ev["event"].wait(self.timeout):
            raise TimeoutError(f"AbletonOSC didn't answer {addr} — is Live open with the control surface enabled?")
        return ev["args"]

    def send(self, addr, *args):
        self.client.send_message(addr, list(args))


def read_live_session() -> Session:
    osc = AbletonOSC()
    tempo = osc.query("/live/song/get/tempo")[0]
    n = osc.query("/live/song/get/num_tracks")[0]
    tracks = []
    for i in range(n):
        name = osc.query("/live/track/get/name", i)[1]
        vol = osc.query("/live/track/get/volume", i)[1]
        pan = osc.query("/live/track/get/panning", i)[1]
        mute = bool(osc.query("/live/track/get/mute", i)[1])
        solo = bool(osc.query("/live/track/get/solo", i)[1])
        devs = list(osc.query("/live/track/get/devices/name", i)[1:])
        has_midi = bool(osc.query("/live/track/get/has_midi_input", i)[1])
        clips = []
        try:
            names = osc.query("/live/track/get/clips/name", i)[1:]
            for ci, cname in enumerate(names):
                if cname:
                    fp = ""
                    if not has_midi:
                        try: fp = osc.query("/live/clip/get/file_path", i, ci)[2]
                        except Exception: pass
                    clips.append({"slot": ci, "name": cname, "file_path": fp})
        except Exception:
            pass
        tracks.append(Track(i, name, vol, pan, mute, solo, False, devs, clips, has_midi))
    return Session(tempo=tempo, tracks=tracks)


def read_session() -> Session:
    if MOCK:
        d = json.load(open(MOCK_PATH))
        return Session(tempo=d["tempo"], tracks=[Track(**t) for t in d["tracks"]],
                       returns=d.get("returns", []), master_devices=d.get("master_devices", []))
    return read_live_session()


# --------------------------------------------------------------- writes

def create_midi_track(name="foldback"):
    if MOCK: return {"mock": True, "track": name}
    osc = AbletonOSC()
    osc.send("/live/song/create_midi_track", -1)
    n = osc.query("/live/song/get/num_tracks")[0]
    osc.send("/live/track/set/name", n - 1, name)
    return {"track_index": n - 1}


def create_audio_track(name="foldback audio"):
    if MOCK: return {"mock": True, "track": name}
    osc = AbletonOSC()
    osc.send("/live/song/create_audio_track", -1)
    n = osc.query("/live/song/get/num_tracks")[0]
    osc.send("/live/track/set/name", n - 1, name)
    return {"track_index": n - 1}


def set_tempo(bpm: float):
    if MOCK: return {"mock": True, "tempo": bpm}
    AbletonOSC().send("/live/song/set/tempo", float(bpm))
    return {"tempo": bpm}
