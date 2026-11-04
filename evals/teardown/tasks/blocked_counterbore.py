from build123d import *
# A valid solid with screw access obstructed by a bridge over the counterbore.
with BuildPart() as bp:
    Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with Locations((0, 0, 6)):
        CounterBoreHole(2.2, 4.5, 3, depth=6)
    with Locations((-9, 0, 6), (9, 0, 6)):
        Box(4, 16, 16, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with Locations((0, 0, 18)):
        Box(22, 16, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
result = bp.part

# Portable output hook: AgentCAD captures show_object; other adapters select result.
try:
    show_object(result)
except NameError:
    pass
