import json

from aiad_platform import __version__
from aiad_platform.cli import main


def test_version_json(capsys):
    assert main(["version", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"version": __version__}
