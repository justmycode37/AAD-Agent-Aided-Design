import pytest

from aad_platform import config
from aad_platform.daemon.handlers import viewer_mode


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AAD_HOME", str(tmp_path))
    return tmp_path


def test_round_trip_and_defaults():
    assert config.get("viewer") == "auto"
    assert config.get("engine.memory") == "auto"
    config.set_value("viewer", "off")
    assert config.get("viewer") == "off"


def test_engine_memory_is_saved_as_toml_and_resolves_to_bytes(home):
    config.set_value("engine.memory", "8GiB")
    assert config.get("engine.memory") == "8GiB"
    assert config.engine_memory_limit_bytes() == 8 * 1024 ** 3
    assert (home / "config.toml").read_text() == 'viewer = "auto"\n\n[engine]\nmemory = "8GiB"\n'


@pytest.mark.parametrize("value", ["0", "-1MiB", "2GB", "infinite", str(2 ** 80)])
def test_engine_memory_rejects_invalid_byte_sizes(value):
    with pytest.raises(config.ConfigError, match="engine.memory"):
        config.set_value("engine.memory", value)


def test_build_backend_passes_configured_memory_budget(monkeypatch, tmp_path):
    from aad.engine import jobs
    from aad.engine.types import BuildResult
    from aad_platform.daemon import backend

    config.set_value("engine.memory", "6GiB")
    seen = {}

    def fake_run_build(request, *, memory_limit_bytes):
        seen["request"] = request
        seen["memory_limit_bytes"] = memory_limit_bytes
        return BuildResult(ok=True, status="draft")

    monkeypatch.setattr(jobs, "run_build", fake_run_build)
    result = backend.run_build(
        source="result = Box(1, 1, 1)",
        project_dir=tmp_path,
        job_dir=tmp_path / "job",
        rev=1,
        kind="part",
        units="mm",
        params={},
        spec={},
        material=None,
        process=None,
        preview=False,
        timeout_s=60,
    )
    assert seen["memory_limit_bytes"] == 6 * 1024 ** 3
    assert result["ok"]


def test_fallback_scene_worker_gets_configured_memory_budget(monkeypatch, tmp_path):
    import sys

    from aad.engine import memory
    from aad_platform.daemon import backend

    config.set_value("engine.memory", "4GiB")
    brep = tmp_path / "part.brep"
    brep.write_bytes(b"fixture")
    out_dir = tmp_path / "scene"
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        (out_dir / "scene.json").write_text('{"scene_id":"fixture"}')

    monkeypatch.setattr(backend.subprocess, "run", fake_run)
    assert backend.stub_scene(brep, out_dir, "fixture", 1, "mm") == {"scene_id": "fixture"}
    argv = seen["argv"]
    assert argv[4] == str(memory.__file__)
    assert argv[5] == str(memory.bootstrap_address_space_budget(4 * 1024 ** 3).bytes)
    assert argv[6] == sys.executable
    assert "install_worker_memory_budget" in argv[10]
    assert seen["kwargs"] == {"check": True, "capture_output": True, "timeout": 120}


def test_fallback_scene_worker_runs_with_configured_memory_budget(tmp_path):
    from build123d import Box, export_brep
    from aad_platform.daemon import backend

    config.set_value("engine.memory", "4GiB")
    brep = tmp_path / "part.brep"
    assert export_brep(Box(2, 3, 4), str(brep))
    scene = backend.stub_scene(brep, tmp_path / "scene", "fixture", 1, "mm")
    assert scene["project"] == "fixture"
    assert scene["triangles"] > 0


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

    from aad_platform.daemon.handlers import Handlers

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
    """Regression: a nested aad_show without a client looked like a call from an open local page."""
    import asyncio

    from aad_platform.daemon.handlers import Handlers

    handlers = Handlers()
    seen = []

    async def build(_params):
        return {"structured": {"ok": True, "status": "draft", "rev": 2}}

    async def show(params):
        seen.append(params)
        return {"structured": {"ok": True}}

    monkeypatch.setattr(handlers, "aad_build", build)
    monkeypatch.setattr(handlers, "aad_show", show)
    client = {"supports_ui": True}
    asyncio.run(handlers.aad_set_param({"project": "p", "base_rev": 1, "name": "w", "value": 2, "client": client}))
    assert seen == [{"project": "p", "rev": 2, "client": client}]
