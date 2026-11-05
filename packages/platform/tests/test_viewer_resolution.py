import pytest
from aad_platform import viewer


def test_missing_runtime_does_not_silently_use_stub(tmp_path, monkeypatch):
    monkeypatch.delenv('AAD_VIEWER_HTML', raising=False)
    monkeypatch.setattr(viewer, '_HERE', tmp_path / 'packages/platform/src/aad_platform/viewer')
    viewer._HERE.mkdir(parents=True)
    (viewer._HERE / 'stub_viewer.html').write_text('legacy')
    with pytest.raises(FileNotFoundError, match='full AAD viewer is missing'):
        viewer.viewer_path()


def test_packaged_runtime_is_preferred_and_workspace_build_is_supported(tmp_path, monkeypatch):
    monkeypatch.delenv('AAD_VIEWER_HTML', raising=False)
    directory = tmp_path / 'packages/platform/src/aad_platform/viewer'
    directory.mkdir(parents=True)
    monkeypatch.setattr(viewer, '_HERE', directory)
    built = tmp_path / 'viewer/dist/aad-viewer.html'
    built.parent.mkdir(parents=True)
    built.write_text('workspace')
    assert viewer.viewer_path() == built
    packaged = directory / 'aad-viewer.html'
    packaged.write_text('packaged')
    assert viewer.viewer_path() == packaged


def test_explicit_override_is_honored_but_missing_override_is_not_ignored(tmp_path, monkeypatch):
    override = tmp_path / 'custom.html'
    monkeypatch.setenv('AAD_VIEWER_HTML', str(override))
    with pytest.raises(FileNotFoundError, match='AAD_VIEWER_HTML does not exist'):
        viewer.viewer_path()
    override.write_text('explicit')
    assert viewer.viewer_path() == override
