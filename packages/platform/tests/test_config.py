import pytest

from aiad_platform import config
from aiad_platform.daemon.handlers import viewer_mode


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AIAD_HOME", str(tmp_path))
    return tmp_path


def test_round_trip_and_defaults():
    assert config.get("viewer") == "auto"
    config.set_value("viewer", "off")
    assert config.get("viewer") == "off"


@pytest.mark.parametrize(("key", "value"), [("viewer", "sideways"), ("nope", "auto")])
def test_rejects_bad_values(key, value):
    with pytest.raises(config.ConfigError):
        config.set_value(key, value)


def test_broken_file_falls_back_for_get_but_load_reports_it(home):
    (home / "config.toml").write_text("not = [valid")
    assert config.get("viewer") == "auto"
    with pytest.raises(config.ConfigError):
        config.load()


@pytest.mark.parametrize(
    ("setting", "client", "expected"),
    [
        ("auto", {"supports_ui": True}, "inline"),
        ("auto", {"supports_ui": False}, "window"),
        ("auto", None, "window"),
        ("inline", {"supports_ui": False}, "inline"),
        ("window", {"supports_ui": True}, "window"),
        ("off", {"supports_ui": True}, "none"),
    ],
)
def test_viewer_mode(setting, client, expected):
    config.set_value("viewer", setting)
    assert viewer_mode(client) == expected


@pytest.mark.parametrize(("setting", "opens"), [("auto", False), ("window", True)])
def test_browser_opens_only_when_windows_were_requested(setting, opens, monkeypatch):
    import asyncio

    from aiad_platform.daemon.handlers import Handlers

    config.set_value("viewer", setting)
    opened = []

    class FakeWindow:
        def open_once(self, project):
            opened.append(project)

        def url(self, project):
            return f"http://127.0.0.1:1/p/{project}"

    handlers = Handlers()

    async def ensure():
        return FakeWindow()

    monkeypatch.setattr(handlers, "_ensure_window", ensure)
    viewer = asyncio.run(handlers._viewer_for("p", {"supports_ui": False}))
    assert viewer == {"mode": "window", "url": "http://127.0.0.1:1/p/p"}
    assert bool(opened) is opens


def test_set_param_shows_with_the_callers_client(monkeypatch):
    """Regression: a nested aiad_show without a client looked like a call from an open local page."""
    import asyncio

    from aiad_platform.daemon.handlers import Handlers

    handlers = Handlers()
    seen = []

    async def build(_params):
        return {"structured": {"ok": True, "status": "draft", "rev": 2}}

    async def show(params):
        seen.append(params)
        return {"structured": {"ok": True}}

    monkeypatch.setattr(handlers, "aiad_build", build)
    monkeypatch.setattr(handlers, "aiad_show", show)
    client = {"supports_ui": True}
    asyncio.run(handlers.aiad_set_param({"project": "p", "base_rev": 1, "name": "w", "value": 2, "client": client}))
    assert seen == [{"project": "p", "rev": 2, "client": client}]
