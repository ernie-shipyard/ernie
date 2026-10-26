from foldback import hum2midi, live


def test_bar_one_starts_after_the_count_in():
    # beats from bar 1: a note a 16th early snaps to the downbeat, count-in noise and anything past 2 bars is dropped
    q = [(60, -2.0, 0.5, 90), (62, -0.25, 1.0, 90), (64, 4.0, 1.0, 90), (65, 8.0, 1.0, 90)]
    assert hum2midi.align_to_bars(q, bars=2) == [(62, 0.0, 1.0, 90), (64, 4.0, 1.0, 90)]


def test_a_rest_in_bar_one_is_kept():
    assert hum2midi.align_to_bars([(60, 4.5, 1.0, 90)], bars=2) == [(60, 4.5, 1.0, 90)]


def test_clip_length():
    assert hum2midi.clip_length([(60, 0, 1, 90)], bars=2) == 8
    assert hum2midi.clip_length([(60, 4.5, 1, 90)]) == 8
    assert hum2midi.clip_length([]) == 4


class FakeOSC:
    def __init__(self):
        self.sent = []

    def send(self, addr, *args):
        self.sent.append((addr, args))

    def query(self, addr, *args):
        return {"/live/song/get/num_tracks": (5,), "/live/clip_slot/get/has_clip": (*args, True)}[addr]


def test_take_lands_as_a_clip_in_the_new_tracks_first_slot(monkeypatch):
    osc = FakeOSC()
    monkeypatch.setattr(live, "MOCK", False)
    monkeypatch.setattr(live, "osc_client", lambda: osc)
    out = live.create_midi_track("bass", [(48, 0.0, 1.0, 100), (50, 1.5, 0.5, 80)], 8)
    assert out == {"track_index": 4, "clip_slot": 0}
    assert ("/live/clip_slot/create_clip", (4, 0, 8.0)) in osc.sent
    assert ("/live/clip/add/notes", (4, 0, 48, 0.0, 1.0, 100, False, 50, 1.5, 0.5, 80, False)) in osc.sent
    assert ("/live/clip/set/name", (4, 0, "bass")) in osc.sent
