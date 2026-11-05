"""Generate a raw laser-panel SVG/DXF and its deterministic svg-1 scene."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aad_drafting.io2d import export_2d
from aad_drafting.model2d import Sheet2D
from aad_drafting.scene2d import make_scene


SAMPLE_DIR = Path(__file__).resolve().parents[1] / "samples" / "native-2d"


def build_panel() -> Sheet2D:
    """Create a laser panel as native source geometry, not a drawing sheet."""
    panel = Sheet2D("laser_panel", units="mm")
    panel.rectangle("outline", width=140, height=90, origin=(-70, -45), layer="cut")
    for name, center in (
        ("mount_lower_left", (-55, -30)),
        ("mount_lower_right", (55, -30)),
        ("mount_upper_left", (-55, 30)),
        ("mount_upper_right", (55, 30)),
    ):
        panel.circle(name, center, radius=2.2, layer="cut")
    panel.circle("connector_left", (-20, 0), radius=5, layer="cut")
    panel.circle("connector_right", (20, 0), radius=5, layer="cut")
    panel.rectangle("switch_opening", width=16, height=8, origin=(-8, 19), layer="cut")
    panel.line("fold_score", (0, -35), (0, 35), layer="score")
    panel.line("alignment_score", (-35, 0), (35, 0), layer="score")
    panel.text("panel_label", "AAD LASER PANEL", (-62, -41), height=3, layer="engrave")
    panel.text("revision_mark", "REV A", (47, 38), height=2, layer="engrave")
    return panel


def write_sample(out: Path) -> dict[str, Any]:
    panel = build_panel()
    context = SimpleNamespace(
        project="native-2d-laser-panel",
        project_dir=out,
        job_dir=out,
        rev=1,
        units="mm",
        sheet=panel,
    )
    svg = export_2d(context, "svg", out / "laser-panel.svg")
    dxf = export_2d(context, "dxf", out / "laser-panel.dxf")
    scene = make_scene(context, out)
    return {"svg": svg, "dxf": dxf, "scene": scene}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=SAMPLE_DIR,
                        help="directory for laser-panel.svg, laser-panel.dxf and scene artifacts")
    args = parser.parse_args()
    result = write_sample(args.out)
    print(f"SVG: {result['svg']}")
    print(f"DXF: {result['dxf']}")
    print(f"Scene: {args.out.resolve() / 'scene.json'}")


if __name__ == "__main__":
    main()
