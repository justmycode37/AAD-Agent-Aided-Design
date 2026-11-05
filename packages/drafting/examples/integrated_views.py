"""Generate a complete section/detail drawing through the public sheet API."""
from pathlib import Path
from types import SimpleNamespace
from aad_drafting import make_drawing
from examples.section_detail import build_drilled_plate


def generate(out=None):
    root = Path(__file__).resolve().parents[1]
    return make_drawing(SimpleNamespace(shape=build_drilled_plate(), units="mm", name="Drilled plate section and detail"), {
        "sheet":"A3", "dimensions":"none",
        "views":[{"name":"section a-a", "kind":"section", "plane":"XY", "offset":0},
                 {"name":"detail b", "kind":"detail", "parent":"top", "center":(-30,-10), "radius":15, "scale":2.5}],
        "title_block":{"drawing_no":"AAD-S4-002"},
    }, out or root/"samples"/"integrated-views")

if __name__ == "__main__":
    result=generate()
    print(result.summary)
    print(result.checks)
