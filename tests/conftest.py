import pytest


@pytest.fixture(autouse=True)
def _private_config(tmp_path_factory, monkeypatch):
    """The launcher's list of matters (adhocdocs.gui) and the desktop entry go
    in a directory of the test's own, not the user's."""
    d = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(d / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(d / "data"))
    monkeypatch.setenv("APPDATA", str(d / "appdata"))
