import pytest

from foldback import samples


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    monkeypatch.setattr(samples, "SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("SAMPLE_DIRS", raising=False)


def test_env_is_the_fallback(monkeypatch, tmp_path):
    assert samples.library_dirs() == []
    monkeypatch.setenv("SAMPLE_DIRS", "/a:/b")
    assert samples.library_dirs() == ["/a", "/b"]


def test_chosen_folders_override_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SAMPLE_DIRS", "/a:/b")
    one, two = tmp_path / "Splice", tmp_path / "Drums"
    one.mkdir(), two.mkdir()
    assert samples.set_library_dirs([str(one), str(two), str(one)]) == [str(one), str(two)]
    assert samples.library_dirs() == [str(one), str(two)]


def test_missing_folder_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Not a folder"):
        samples.set_library_dirs([str(tmp_path / "nope")])
    assert samples.library_dirs() == []
