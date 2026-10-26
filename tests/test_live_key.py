import pytest

from foldback import live


class FakeOSC:
    """Answers AbletonOSC song getters from a dict; a missing address times out like an unpatched AbletonOSC."""

    def __init__(self, **props):
        self.props = props

    def query(self, addr, *args):
        name = addr.rsplit("/", 1)[-1]
        if name not in self.props:
            raise TimeoutError(addr)
        return (self.props[name],)


@pytest.fixture(autouse=True)
def scale_mode_supported(monkeypatch):
    monkeypatch.setattr(live, "_scale_mode_supported", True)


@pytest.mark.parametrize("root, scale, want", [
    (9, "Minor", "A minor"),
    (6, "Major", "F# major"),
    (2, "Dorian", "D minor"),
    (7, "Mixolydian", "G major"),
    (0, "Whole Tone", "C"),
])
def test_scale_mode_on_reads_the_key(root, scale, want):
    assert live.read_key(FakeOSC(root_note=root, scale_name=scale, scale_mode=True)) == want


def test_scale_mode_off_means_no_key():
    assert live.read_key(FakeOSC(root_note=9, scale_name="Minor", scale_mode=False)) is None


def test_without_scale_mode_c_major_counts_as_unset():
    assert live.read_key(FakeOSC(root_note=0, scale_name="Major")) is None
    assert live._scale_mode_supported is False
    assert live.read_key(FakeOSC(root_note=9, scale_name="Minor")) == "A minor"


def test_no_key_support_at_all_is_not_an_error():
    assert live.read_key(FakeOSC()) is None
