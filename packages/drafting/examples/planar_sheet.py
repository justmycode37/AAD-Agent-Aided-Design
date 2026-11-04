"""Produce a dimensioned drawing directly from an editable XY sheet."""
from pathlib import Path
from types import SimpleNamespace

from aiad_drafting import DrawingRequest, make_drawing, sheet2d


def model(units="mm"):
    factor = 1 if units == "mm" else 1 / 25.4
    panel = sheet2d("native_laser_panel", units=units)
    panel.rectangle("outline", 100 * factor, 60 * factor, origin=(-50 * factor, -30 * factor))
    for index, (x, y) in enumerate(((-40, -20), (40, -20), (-40, 20), (40, 20))):
        panel.circle(f"mount{index + 1}", (x * factor, y * factor), 2.2 * factor)
    panel.rectangle("slot", 12 * factor, 4 * factor, origin=(-6 * factor, -2 * factor))
    panel.text("mark", "AIAD", (-7 * factor, 8 * factor), 3 * factor)
    return panel


def generate(out):
    panel = model()
    ctx = SimpleNamespace(kind="2d", sheet=panel, shape=None, units=panel.units, process="laser", name=panel.name)
    return make_drawing(ctx, DrawingRequest(sheet="A3", title_block={"title": "NATIVE LASER PANEL", "drawing_no": "S4-2D-01"}), out)


if __name__ == "__main__":
    result = generate(Path(__file__).parents[1] / "samples" / "planar-sheet")
    print(result.summary)
    print("ok:", result.ok)
    for check in result.checks:
        if check["status"] == "fail":
            print(check)
