"""Confined writers must not follow a directory swapped by another process."""
import os
from pathlib import Path

import pytest

from aad_drafting import export_2d, make_scene
from aad_drafting._safe_output import OutputDirectory
from aad_drafting.model2d import Sheet2D

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX directory-handle adversarial tests')


def write(kind, job, parent):
    sheet = Sheet2D('panel')
    sheet.rectangle('outline', 4, 3)
    ctx = {'sheet': sheet, 'units': 'mm', 'job_dir': job, 'project': 'panel', 'rev': 1}
    if kind == 'scene':
        return make_scene(ctx, parent)
    return export_2d(ctx, kind, parent / f'panel.{kind}')


@pytest.mark.parametrize('kind', ['svg', 'dxf', 'scene'])
def test_directory_replaced_during_creation_cannot_redirect_output(tmp_path, monkeypatch, kind):
    job, outside = tmp_path/'job', tmp_path/'outside'
    job.mkdir()
    outside.mkdir()
    parent = job/'raced'
    original = os.mkdir

    def replace_with_symlink(name, mode=0o777, *, dir_fd=None):
        if name == 'raced' and dir_fd is not None:
            parent.symlink_to(outside, target_is_directory=True)
            return
        return original(name, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, 'mkdir', replace_with_symlink)
    with pytest.raises(OSError):
        write(kind, job, parent)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('kind', ['svg', 'dxf', 'scene'])
def test_directory_replaced_after_open_keeps_staging_and_rollback_pinned(tmp_path, monkeypatch, kind):
    job, outside = tmp_path/'job', tmp_path/'outside'
    job.mkdir()
    outside.mkdir()
    parent, pinned = job/'raced', job/'pinned'
    parent.mkdir()
    original = OutputDirectory.stage
    swapped = False

    def replace_after_open(self, name, data):
        nonlocal swapped
        if not swapped:
            parent.rename(pinned)
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original(self, name, data)

    monkeypatch.setattr(OutputDirectory, 'stage', replace_after_open)
    with pytest.raises(OSError, match='changed during publication'):
        write(kind, job, parent)
    assert list(outside.iterdir()) == []
    assert list(pinned.iterdir()) == []


@pytest.mark.parametrize('kind', ['svg', 'dxf', 'scene'])
def test_existing_symlink_outside_job_is_rejected_before_writing(tmp_path, kind):
    job, outside = tmp_path/'job', tmp_path/'outside'
    job.mkdir()
    outside.mkdir()
    parent = job/'link'
    parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='must be under ctx.job_dir'):
        write(kind, job, parent)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('kind', ['svg', 'dxf', 'scene'])
def test_directory_replaced_while_publishing_restores_prior_files_using_handles(tmp_path, monkeypatch, kind):
    job, outside = tmp_path/'job', tmp_path/'outside'
    job.mkdir()
    outside.mkdir()
    parent, pinned = job/'raced', job/'pinned'
    parent.mkdir()
    names = ['scene.bin.gz', 'scene.json'] if kind == 'scene' else [f'panel.{kind}']
    for name in names:
        (parent/name).write_bytes(b'previous artifact')
    original = OutputDirectory.replace
    swapped = False

    def replace_while_publishing(self, source, destination):
        nonlocal swapped
        if source.endswith('.tmp') and not swapped:
            parent.rename(pinned)
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original(self, source, destination)

    monkeypatch.setattr(OutputDirectory, 'replace', replace_while_publishing)
    with pytest.raises(OSError, match='changed during publication'):
        write(kind, job, parent)
    assert list(outside.iterdir()) == []
    assert sorted(path.name for path in pinned.iterdir()) == sorted(names)
    assert all((pinned/name).read_bytes() == b'previous artifact' for name in names)


@pytest.mark.parametrize('kind', ['svg', 'dxf', 'scene'])
@pytest.mark.parametrize('replacement', ['symlink', 'regular'])
@pytest.mark.parametrize('phase', ['staged', 'installed'])
def test_substituted_source_or_installed_entry_cannot_publish_changed_artifact(tmp_path, monkeypatch, kind, replacement, phase):
    job = tmp_path/'job'
    job.mkdir()
    parent = job/'outputs'
    parent.mkdir()
    sentinel = tmp_path/'outside.txt'
    sentinel.write_bytes(b'outside sentinel')
    names = ['scene.bin.gz', 'scene.json'] if kind == 'scene' else [f'panel.{kind}']
    for name in names:
        (parent/name).write_bytes(b'previous artifact')
    original = OutputDirectory.replace
    swapped = False

    def substitute_staged_source(self, source, destination):
        nonlocal swapped
        if source.endswith('.tmp') and not swapped:
            swapped = True
            if phase == 'installed':
                original(self, source, destination)
            entry = self.path/(source if phase == 'staged' else destination)
            entry.unlink()
            if replacement == 'symlink':
                entry.symlink_to(sentinel)
            else:
                entry.write_bytes(b'substituted regular file')
            if phase == 'installed':
                return
        return original(self, source, destination)

    monkeypatch.setattr(OutputDirectory, 'replace', substitute_staged_source)
    with pytest.raises(OSError, match='staged file changed'):
        write(kind, job, parent)
    assert sentinel.read_bytes() == b'outside sentinel'
    assert sorted(path.name for path in parent.iterdir()) == sorted(names)
    for name in names:
        assert not (parent/name).is_symlink()
        assert (parent/name).read_bytes() == b'previous artifact'
