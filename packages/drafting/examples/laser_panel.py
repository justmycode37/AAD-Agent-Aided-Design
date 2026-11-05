"""Build a simple, editable laser-cut mounting panel."""

from aad_drafting.model2d import sheet2d


panel = sheet2d("laser_panel", units="mm")
with panel:
    panel.rectangle("outline", width=100, height=60, origin=(-50, -30))
    panel.circle("mount_lower_left", (-40, -20), radius=2.2)

# Pattern the first pair across the panel, then copy both across the height.
panel = panel.linear_pattern("mount_lower_left", count=2, spacing=(80, 0))
panel = panel.linear_pattern(
    ("mount_lower_left", "mount_lower_left.linear1"),
    count=2,
    spacing=(0, 40),
    name_prefix="upper",
)

panel.line("center_score", (-44, 0), (44, 0), layer="score")
panel.text("mark", "AAD", (-5, 3), height=3, layer="engrave")

if __name__ == "__main__":
    face = panel.to_face(layers=("cut",))
    print(f"{panel.name}: {len(panel.entities)} editable entities, cut area {face.area:.2f} {panel.units}²")
