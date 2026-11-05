"""Ship repository guidance in wheels and make sdists self-contained."""
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        bundled = root / "src/aad_quality/guidance"
        if bundled.is_dir():
            return  # A source distribution already carries the guidance.
        guidance = root.parents[1] / "skills"
        if not guidance.is_dir():
            raise FileNotFoundError("aad-quality needs its skills guidance to build")
        destination = "src/aad_quality/guidance" if self.target_name == "sdist" else "aad_quality/guidance"
        build_data.setdefault("force_include", {})[str(guidance)] = destination
