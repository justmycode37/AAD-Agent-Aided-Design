from build123d import *
W = 80
SPACING = 66
with BuildPart() as bp:
    Box(W, 40, 6)
    with GridLocations(SPACING, 26, 2, 2):
        Hole(2.2)
result = bp.part

# Portable output hook: AgentCAD captures show_object; other adapters select result.
try:
    show_object(result)
except NameError:
    pass
