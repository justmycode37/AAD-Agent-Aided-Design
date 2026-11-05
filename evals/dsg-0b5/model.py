"""7-speed longitudinal dual-clutch gearbox, Audi S tronic 0B5 / DL501 layout. Design v5, reference-informed.

Measured or published: 89 mm shaft spacing, tooth counts (DL501 data sheet, codes NHS/NSC), gear order, axial positions and
gear diameters (Audi side-view illustration scaled by the shaft spacing, about 0.567 mm/px), 4-6 and 7-5 synchronisers on the
input shafts, overall length about 1045 mm (side view and silhouette fit agree). Housing wall radii are fitted parameters
from three pictures, not traced outlines.
ASSUMED: normal modules and so helix angles, front-axle shaft position, bevel/crown tooth form (stand-ins), most wall thickness,
and everything hidden from those pictures.

Axes: input shafts on Z at (0, 0), output at (0, -89), +Z from engine to tail. z = 0 is about the bell mating face.
"""
from math import acos, atan, atan2, cos, degrees, hypot, pi, radians, sin, sqrt, tan

from aad import *

P = params(
    housing=1,    # 1 complete, 0 hide the housing to see the gear train
    cutaway=0,    # 1 remove a window from the housing, like the factory cutaway
    bolts=1,
    bearing_balls=1,
)

CD = 89.0                 # published shaft spacing
OUT = (0.0, -CD)
FRONT = (64.0, -160.0)    # front-axle drive shaft, position assumed
ALPHA_N = radians(20.0)
BACKLASH = 0.08
FIT = 0.02

STEEL = (0.70, 0.72, 0.75)
GOLD = (0.82, 0.62, 0.20)
BLUE = (0.20, 0.42, 0.72)
DARK = (0.22, 0.23, 0.25)
CASE = (0.74, 0.75, 0.77)
BROWN = (0.33, 0.20, 0.16)
ORANGE = (0.86, 0.55, 0.12)
RED = (0.72, 0.13, 0.10)
CHIP = (0.72, 0.62, 0.30)

PARTS = []


def add(shape, name, color=STEEL):
    if not shape.is_valid or shape.volume <= 0:
        raise ValueError(f"{name}: invalid or empty solid")
    shape.label = name
    shape.color = Color(*color)
    PARTS.append(shape)
    return shape


# ---------------------------------------------------------------- primitives
def rev(pts, x=0.0, y=0.0):
    """Solid of revolution about the Z-parallel axis through (x, y). pts: closed (r, z) polygon."""
    wire = Wire.make_polygon([Vector(r, 0, z) for r, z in pts], close=True)
    return Pos(x, y, 0) * revolve(Face(wire), Axis.Z)


def ring(ri, ro, z0, z1, x=0.0, y=0.0):
    return rev([(ri, z0), (ro, z0), (ro, z1), (ri, z1)], x, y)


def rod(r, z0, z1, x=0.0, y=0.0):
    return rev([(0, z0), (r, z0), (r, z1), (0, z1)], x, y)


def box(x0, x1, y0, y1, z0, z1):
    return Pos(x0, y0, z0) * Box(x1 - x0, y1 - y0, z1 - z0, align=(Align.MIN, Align.MIN, Align.MIN))


def x_cyl(r, x0, x1, y, z):
    """Cylinder along X."""
    return Pos(x0, y, z) * Rot(0, 90, 0) * Cylinder(r, x1 - x0, align=(Align.CENTER, Align.CENTER, Align.MIN))


def y_cyl(r, y0, y1, x, z):
    return Pos(x, y0, z) * Rot(-90, 0, 0) * Cylinder(r, y1 - y0, align=(Align.CENTER, Align.CENTER, Align.MIN))


# ---------------------------------------------------------------- gears
def inv(a):
    return tan(a) - a


def polar(r, a):
    return Vector(r * cos(a), r * sin(a), 0)


def gear_data(z, mn, beta):
    alpha_t = atan(tan(ALPHA_N) / cos(beta))
    mt = mn / cos(beta)
    rp = z * mt / 2
    return alpha_t, mt, rp, rp * cos(alpha_t), rp + mn, rp - 1.25 * mn


def gear_wire(z, mn, beta, n=7):
    alpha_t, mt, rp, rb, ra, rf = gear_data(z, mn, beta)
    half = (pi * mt / 2 - BACKLASH / 2) / (2 * rp)
    at = lambda r: half + inv(alpha_t) - inv(acos(min(1.0, rb / r)))
    r0 = max(rb, rf)
    radii = [r0 + (ra - r0) * (i / n) ** 1.3 for i in range(n + 1)]
    pitch = 2 * pi / z
    edges = []
    for k in range(z):
        c = k * pitch
        right = [polar(r, c - at(r)) for r in radii]
        left = [polar(r, c + at(r)) for r in reversed(radii)]
        if rf < rb:
            edges.append(Edge.make_line(polar(rf, c - at(rb)), right[0]))
        edges.append(Edge.make_spline(right))
        edges.append(Edge.make_three_point_arc(right[-1], polar(ra, c), left[0]))
        edges.append(Edge.make_spline(left))
        if rf < rb:
            edges.append(Edge.make_line(left[-1], polar(rf, c + at(rb))))
        a1, a2 = c + at(r0), c + pitch - at(r0)
        edges.append(Edge.make_three_point_arc(polar(rf, a1), polar(rf, (a1 + a2) / 2), polar(rf, a2)))
    return Wire(edges)


def helical(z, mn, beta, width, z0, x=0.0, y=0.0, hand=1, phase=0.0, bore=0.0):
    """Involute helical gear, transverse-profile twist extrusion. hand = +1 right, -1 left."""
    rp = gear_data(z, mn, beta)[2]
    twist = degrees(hand * width * tan(beta) / rp)
    face = Face(gear_wire(z, mn, beta))
    if twist:
        solid = Solid.extrude_linear_with_rotation(face, Vector(0, 0, 0), Vector(0, 0, width), twist)
    else:
        solid = extrude(face, width, dir=(0, 0, 1))
    if bore:
        solid = solid - rod(bore, -1, width + 1)
    return Pos(x, y, z0) * Rot(0, 0, degrees(phase)) * solid


def cone_gear(z, mn, width, ratio, z0, x=0.0, y=0.0):
    """Straight tapered gear: a stand-in for bevel/hypoid/beveloid teeth, not their true flank form."""
    w0 = gear_wire(z, mn, 0.0, n=4)
    w1 = Pos(0, 0, width) * gear_wire(z, mn * ratio, 0.0, n=4)
    return Pos(x, y, z0) * Solid.make_loft([w0, w1], ruled=True)


def meshed(z1, z2, phi, t1=0.0):
    """Angle of gear 2 meshing with gear 1 (rotated t1); phi points from gear 1 to gear 2."""
    return phi + pi + pi / z2 - (z1 / z2) * (t1 - phi)



# Gear table from the data sheet. z_start, width, teeth input, teeth output, normal module, input bore, output bore.
GEARS = {
    "4": (309.0, 20.0, 40, 41, 2.00, 20.02, 27.02),
    "6": (376.0, 21.0, 48, 30, 2.00, 20.02, 27.02),
    "2": (401.5, 25.5, 20, 43, 2.50, 20.02, 27.02),
    "1": (534.0, 27.0, 13, 48, 2.50, 15.02, 27.02),
    "3": (598.0, 25.0, 32, 45, 2.00, 17.02, 27.02),
    "7": (628.0, 21.0, 52, 27, 2.00, 17.02, 25.02),
    "5": (692.0, 19.0, 47, 37, 2.00, 17.02, 27.02),
}
SUB = {"1": 1, "3": 1, "5": 1, "7": 1, "2": 2, "4": 2, "6": 2}
RATIOS = {g: round(v[3] / v[2], 3) for g, v in GEARS.items()}
PUBLISHED = {"1": 3.692, "2": 2.150, "3": 1.406, "4": 1.025, "5": 0.787, "6": 0.625, "7": 0.519}
for g, want in PUBLISHED.items():
    if abs(RATIOS[g] - want) > 0.001:
        raise ValueError(f"gear {g}: ratio {RATIOS[g]} is not the published {want}")
PHI = -pi / 2

for g, (z0, w, zi, zo, mn, bi, bo) in GEARS.items():
    beta = acos(mn * (zi + zo) / (2 * CD))
    color = GOLD if SUB[g] == 1 else BLUE
    add(helical(zi, mn, beta, w, z0, 0, 0, hand=1, phase=0.0, bore=bi), f"input_gear_{g}", color)
    add(helical(zo, mn, beta, w, z0, *OUT, hand=-1, phase=meshed(zi, zo, PHI), bore=bo), f"output_gear_{g}", STEEL)

# Reverse: 18t input, idler, 53t output gives the published 2.944.
R_Z0, R_W, R_MN, R_BETA = 464.0, 25.0, 2.00, radians(25.0)
Z_RI, Z_RID, Z_RO = 18, 27, 53
RATIOS["R"] = round(Z_RO / Z_RI, 3)
_mt = R_MN / cos(R_BETA)
d1, d2 = _mt * (Z_RI + Z_RID) / 2, _mt * (Z_RID + Z_RO) / 2
_y = -(d1 * d1 - d2 * d2 + CD * CD) / (2 * CD)
_x = -sqrt(d1 * d1 - _y * _y)
IDLER = (_x, _y)
_phi_i = atan2(_y, _x)
_phase_id = meshed(Z_RI, Z_RID, _phi_i)
add(helical(Z_RI, R_MN, R_BETA, R_W, R_Z0, 0, 0, hand=1, bore=15.02), "input_gear_R", BLUE)
add(helical(Z_RID, R_MN, R_BETA, R_W, R_Z0, *IDLER, hand=-1, phase=_phase_id, bore=11.02), "reverse_idler", STEEL)
_phi_o = atan2(OUT[1] - _y, OUT[0] - _x)
_phase_o = _phi_o + pi + pi / Z_RO - (Z_RID / Z_RO) * (_phase_id - _phi_o)
add(helical(Z_RO, R_MN, R_BETA, R_W, R_Z0, *OUT, hand=1, phase=_phase_o, bore=27.02), "output_gear_R", STEEL)
add(rod(11.0, R_Z0 - 10, 500.0, *IDLER), "reverse_idler_pin", DARK)

# ---------------------------------------------------------------- shafts
add(rev([(0, 58), (12.5, 58), (12.5, 497), (20, 497), (20, 531), (15, 531), (15, 564), (17, 564), (17, 722), (20, 722),
         (20, 755), (0, 755)]), "input_shaft_1_solid", GOLD)
add(rev([(13.0, 113), (20, 113), (20, 462), (15, 462), (15, 490), (13.0, 490)]), "input_shaft_2_hollow", BLUE)
add(rev([(0, 272), (25, 272), (25, 293), (27, 293), (27, 626), (25, 626), (25, 650), (27, 650), (27, 726), (25, 726),
         (25, 812), (20, 812), (20, 880), (0, 880)], *OUT), "output_shaft", STEEL)

# ---------------------------------------------------------------- synchronisers
def synchro(name, z0, z1, bore, ro_hub, ro, x, y):
    zc = (z0 + z1) / 2
    hw = (z1 - z0) / 2 - 3
    add(ring(bore, ro_hub, z0 + 3, z1 - 3, x, y), f"{name}_hub", DARK)
    add(rev([(ro_hub + 0.02, zc - hw), (ro, zc - hw), (ro, zc - 3), (ro - 5, zc - 3), (ro - 5, zc + 3), (ro, zc + 3), (ro, zc + hw),
             (ro_hub + 0.02, zc + hw)], x, y), f"{name}_sleeve", STEEL)
    add(ring(bore, ro_hub, z0, z0 + 3, x, y), f"{name}_cone_a", CHIP)
    add(ring(bore, ro_hub, z1 - 3, z1, x, y), f"{name}_cone_b", CHIP)


SYNCHROS = {"S46": (332.0, 376.0, 20.02, 36.0, 48.0, 0.0, 0.0), "S2R": (431.0, 464.0, 27.02, 44.0, 55.0, *OUT),
            "S13": (562.5, 597.0, 27.02, 43.0, 54.0, *OUT), "S75": (650.0, 692.0, 17.02, 38.0, 51.0, 0.0, 0.0)}
for name, (a, b, bore, hub, ro, sx, sy) in SYNCHROS.items():
    synchro(name, a, b, bore, hub, ro, sx, sy)

# ---------------------------------------------------------------- bearings
def bearing(name, z0, width, ri, ro, x=0.0, y=0.0, n=11):
    rm = (ri + ro) / 2
    h = (ro - ri) * 0.22
    d = (ro - ri) * 0.42
    add(ring(ri, rm - h, z0, z0 + width, x, y), f"{name}_inner", STEEL)
    add(ring(rm + h, ro, z0, z0 + width, x, y), f"{name}_outer", STEEL)
    if int(P.bearing_balls):
        ball = Sphere(d / 2) & Box(d, d, d - 0.1)
        for i in range(n):
            a = 2 * pi * i / n
            add(Pos(x + rm * cos(a), y + rm * sin(a), z0 + width / 2) * ball, f"{name}_ball_{i}", DARK)


bearing("brg_is2_front", 278.0, 17.0, 20.02, 42.0, n=9)
bearing("brg_is1_mid", 502.0, 25.0, 20.02, 41.0, n=9)
bearing("brg_is1_rear", 724.0, 25.0, 20.02, 42.0, n=9)
bearing("brg_os_front", 272.0, 20.0, 25.02, 45.0, *OUT)
bearing("brg_os_mid", 505.0, 20.0, 27.02, 45.0, *OUT)
bearing("brg_os_rear", 728.0, 20.0, 25.02, 42.0, *OUT)
bearing("brg_os_tail", 790.0, 22.0, 25.02, 40.0, *OUT)

# ---------------------------------------------------------------- dual clutch, sizes from the side view
add(rev([(38, 94), (111, 94), (111, 150), (106, 150), (106, 100), (38, 100)]), "clutch_outer_basket", CHIP)
add(rev([(12.52, 94), (24, 94), (24, 100), (38, 100), (38, 106), (12.52, 106)]), "clutch_input_hub", STEEL)
add(rev([(12.52, 108), (86, 108), (86, 142), (82, 142), (82, 112), (12.52, 112)]), "k1_inner_carrier", ORANGE)
add(rev([(20.02, 134), (50, 134), (50, 114), (47, 114), (47, 130), (20.02, 130)]), "k2_inner_carrier", BLUE)
_z = 116.0
for i in range(6):
    add(ring(90, 105.9, _z, _z + 2.0), f"k1_steel_{i}", STEEL)
    add(ring(86.02, 102, _z + 2.1, _z + 4.1), f"k1_friction_{i}", DARK)
    _z += 4.3
_z = 114.5
for i in range(4):
    add(ring(54, 76, _z, _z + 1.7), f"k2_steel_{i}", STEEL)
    add(ring(50.02, 72, _z + 1.8, _z + 3.5), f"k2_friction_{i}", BLUE)
    _z += 3.6
add(ring(88, 105.9, 143, 150), "k1_piston", CHIP)
add(ring(52, 80, 134.5, 142), "k2_piston", CHIP)

# ---------------------------------------------------------------- flywheel side
plate = ring(25, 144, 3, 7.4)
for i in range(8):
    a = 2 * pi * i / 8 + pi / 8
    plate = plate - rod(5, 2, 8, 136 * cos(a), 136 * sin(a))
add(plate, "drive_plate", DARK)
add(rev([(12.52, 48), (40, 48), (40, 66), (135, 66), (135, 92), (130, 92), (130, 72), (32, 72), (32, 92), (12.52, 92)]), "dmf_secondary", STEEL)
add(rev([(100, 48), (144, 48), (144, 96), (138, 96), (138, 54), (100, 54)]), "dmf_primary_ring", STEEL)
add(Pos(0, 0, 66) * Torus(120, 9) & box(-160, 160, -160, 160, 54.5, 65.9), "dmf_arc_spring", DARK)

# ---------------------------------------------------------------- ATF pump on the clutch-side wall
add(ring(20.02, 40, 173, 241), "atf_pump_hub", DARK)
add(rod(9, 195, 250, 62, -14), "atf_pump_shaft", DARK)
add(helical(24, 1.5, radians(15), 12, 225, 62, -14, hand=1, bore=9.02), "atf_pump_drive_gear", STEEL)
add(helical(30, 3.0, 0.0, 8, 297, *OUT, hand=1, bore=27.02), "parking_lock_wheel", STEEL)

# ---------------------------------------------------------------- shift rails and forks
RAILS = {"S46": (78.0, -18.0), "S75": (74.0, 26.0), "S2R": (-88.0, -80.0), "S13": (88.0, -70.0)}
RAIL_Z = {"S46": (296.0, 500.0), "S2R": (296.0, 500.0), "S13": (528.0, 722.0), "S75": (528.0, 722.0)}
for name, (rx, ry) in RAILS.items():
    a, b, _, _, ro, sx, sy = SYNCHROS[name]
    zc = (a + b) / 2
    add(rod(8, *RAIL_Z[name], rx, ry), f"rail_{name}", DARK)
    side = 1 if rx > 0 else -1
    arc = ring(ro - 4.5, ro + 9, zc - 2.6, zc + 2.6, sx, sy)
    arc = arc & box(min(sx, sx + side * 90), max(sx, sx + side * 90), sy - 80, sy + 80, zc - 4, zc + 4)
    inner_x = sx + side * (ro + 2)
    arm = box(min(inner_x, side * (abs(rx) - 8.01)), max(inner_x, side * (abs(rx) - 8.01)), ry - 6, ry + 6, zc - 2.6, zc + 2.6)
    add(arc + arm, f"fork_{name}", BROWN)

# ---------------------------------------------------------------- output to front axle, and centre differential
FP_Z0, FP_W, FP_ZP, FP_ZG, FP_MN = 752.0, 36.0, 40, 34, 2.25
_a = hypot(FRONT[0] - OUT[0], FRONT[1] - OUT[1])
FP_BETA = acos(FP_MN * (FP_ZP + FP_ZG) / (2 * _a))
_phi_f = atan2(FRONT[1] - OUT[1], FRONT[0] - OUT[0])
add(helical(FP_ZP, FP_MN, FP_BETA, FP_W, FP_Z0, *OUT, hand=1, bore=25.02), "front_drive_pinion", STEEL)
add(helical(FP_ZG, FP_MN, FP_BETA, FP_W, FP_Z0, *FRONT, hand=-1, phase=meshed(FP_ZP, FP_ZG, _phi_f), bore=15.02), "front_driven_gear", STEEL)
add(rev([(0, 123), (15, 123), (15, 800), (0, 800)], *FRONT), "front_axle_shaft", STEEL)
bearing("brg_front_a", 133.0, 14.0, 15.02, 34.0, *FRONT, n=8)
bearing("brg_front_b", 724.0, 16.0, 15.02, 34.0, *FRONT, n=8)

DZ0, DZ1 = 819.0, 950.0
add(rev([(20.02, DZ0), (71, DZ0), (71, DZ1), (28, DZ1), (28, DZ1 - 6), (65, DZ1 - 6), (65, DZ0 + 6), (20.02, DZ0 + 6)], *OUT), "centre_diff_case", STEEL)
add(cone_gear(36, 2.75, 18, 0.72, DZ0 + 27, *OUT) - rod(20.02, DZ0, DZ0 + 60, *OUT), "crown_gear_front", DARK)
add(Pos(OUT[0], OUT[1], DZ1 - 26) * Rot(180, 0, 0) * cone_gear(44, 2.75, 18, 0.72, 0.0) - rod(28.02, DZ1 - 60, DZ1, *OUT), "crown_gear_rear", DARK)
for i in range(4):
    a = pi / 4 + i * pi / 2
    _c = (OUT[0] + 44 * cos(a), OUT[1] + 44 * sin(a))
    add(Pos(_c[0], _c[1], (DZ0 + DZ1) / 2 + 1) * Rot(0, 0, degrees(a)) * Rot(0, 90, 0) * cone_gear(12, 2.75, 14, 0.8, -7.0), f"centre_diff_pinion_{i}", STEEL)

bearing("brg_tail", 965.0, 18.0, 28.02, 42.0, *OUT)

# Fitted housing parameters: wall radii about the shaft axes and named lobes. Numbers only, no traced outline.
FIT_ZS = [0.0, 14.0, 40.0, 70.0, 100.0, 130.0, 165.0, 200.0, 240.0, 280.0, 320.0, 370.0, 420.0, 470.0, 520.0, 570.0, 620.0, 660.0, 700.0, 740.0, 780.0, 820.0, 860.0, 900.0, 940.0, 985.0]
FIT_RI = [186.0, 178.8, 171.6, 167.4, 169.2, 170.9, 172.7, 169.2, 160.5, 141.4, 122.5, 105.7, 98.1, 91.0, 83.9, 76.8, 69.6, 67.3, 66.8, 67.0, 67.3, 56.2, 30.8, 12.1, 9.2, 6.4]
FIT_RO = [166.8, 160.0, 151.4, 142.9, 134.7, 126.5, 118.4, 111.5, 106.7, 103.8, 102.0, 104.0, 107.9, 112.2, 116.3, 108.5, 100.7, 97.4, 94.2, 92.4, 90.8, 84.6, 78.1, 71.5, 65.9, 60.3]
FIT_DXO = [-8.7, -5.8, -3.2, -1.4, -0.6, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.9, 3.3, 5.7, 9.9, 14.0]
FIT_PARAMS = {"pan_x0": -144.2, "pan_x1": 49.8, "pan_y": -239.1, "pan_z0": 120.4, "pan_z1": 509.7, "fd_y": -180.2, "fd_z": 62.0, "fd_r": 62.0, "tube_x": -180.1, "tube_r": 34.0, "flange_r": 47.6, "boss_x": 164.7, "boss_r": 62.6, "shaft_r": 30.9, "shaft_end": 1059.4, "side_w": 0.0, "side_y": -60.0, "side_z0": 60.0, "side_z1": 300.0, "top_x": 5.8, "top_y": 118.5, "top_r": 33.1, "top_z0": 105.0, "top_z1": 320.0, "tail_x": 40.0, "tail_y": -37.5, "tail_r": 29.4, "tail_z0": 700.0, "tail_z1": 845.9, "fr_x": 51.4, "fr_y": -159.0, "fr_r": 48.0, "fr_z0": 130.0, "fr_z1": 805.0, "bell_x": -10.1, "bell_y": -5.0, "l1_x": 100.0, "l1_y": 110.0, "l1_r": 20.0, "l2_x": -191.4, "l2_y": 95.6, "l2_r": 24.0, "l3_x": 171.0, "l3_y": -74.4, "l3_r": 24.0, "l4_x": -152.5, "l4_y": -92.5, "l4_r": 13.0, "l5_x": 0.0, "l5_y": -175.0, "l5_r": 20.0, "pan_ch": 80.7, "b0_a": 64.1, "b0_z": 201.0, "b0_r": 25.0, "b0_h": 8.5, "b1_a": 69.1, "b1_z": 262.5, "b1_r": 20.3, "b1_h": 12.7, "b2_a": 120.0, "b2_z": 320.0, "b2_r": 16.0, "b2_h": 4.0, "b3_a": 59.1, "b3_z": 426.5, "b3_r": 16.8, "b3_h": 4.9, "b4_a": 112.0, "b4_z": 560.0, "b4_r": 16.0, "b4_h": 4.0, "b5_a": 40.0, "b5_z": 700.0, "b5_r": 12.0, "b5_h": -2.0, "b6_a": 130.0, "b6_z": 800.0, "b6_r": 16.0, "b6_h": 4.0, "b7_a": -20.0, "b7_z": 860.0, "b7_r": 16.0, "b7_h": 4.0}

# ---------------------------------------------------------------- mechatronics, inside the sump
FP = FIT_PARAMS
PAN_Y = FP["pan_y"]
PAN_CH = max(FP["pan_ch"], 1.0)
MX0, MX1, MY0, MY1, MZ0, MZ1 = FP["pan_x0"] + 10, min(FP["pan_x1"] - 10, 38.0), PAN_Y + 24, -168.0, FP["pan_z0"] + 45, FP["pan_z1"] - max(PAN_CH - 24.0, 0.0) - 10.0
add(box(MX0, MX1, MY0, MY1, MZ0, MZ1), "mechatronic_hydraulic_block", (0.62, 0.64, 0.66))
add(box(MX0 + 8, MX1 - 8, MY0 - 4, MY0, MZ0 + 20, MZ1 - 40), "tcm_plate", CHIP)
for i in range(6):
    add(rod(6.5, MZ0 - 24, MZ0, MX0 + 18 + i * 14, MY1 - 16), f"solenoid_{i}", DARK)
add(rod(18, 505, 565, 140, -105), "atf_filter_module", (0.55, 0.56, 0.58))

# ---------------------------------------------------------------- front differential, axis along X
FD_Y, FD_Z = FP["fd_y"], max(FP["fd_z"], 62.0)
add(Pos(FRONT[0], FRONT[1], 123) * Rot(180, 0, 0) * cone_gear(11, 3.5, 26, 0.72, 0.0), "front_hypoid_pinion", STEEL)
add((Pos(88.0, FD_Y, FD_Z) * Rot(0, 90, 0) * cone_gear(30, 3.5, 22, 0.84, 0.0)) - x_cyl(20.02, 80, 114, FD_Y, FD_Z), "front_ring_gear", STEEL)
add(x_cyl(20, 88, 114, FD_Y, FD_Z) - x_cyl(14.02, 87, 115, FD_Y, FD_Z), "front_diff_case", STEEL)
add(x_cyl(14, FP["tube_x"], FP["boss_x"] + 2, FD_Y, FD_Z), "front_side_shaft", DARK)


def side_flange(x0, x1, r):
    f = x_cyl(r, x0, x1, FD_Y, FD_Z)
    for i in range(6):
        a = pi * i / 3
        f = f - x_cyl(5, min(x0, x1) - 1, max(x0, x1) + 1, FD_Y + (r - 11) * cos(a), FD_Z + (r - 11) * sin(a))
    return f


add(side_flange(FP["tube_x"] - 16, FP["tube_x"], FP["flange_r"]), "front_flange_left", DARK)

# Rear splined output, length from the pictures.
SPLINE_LEN = 55.0
SHAFT_END = max(FP["shaft_end"], 1000.0)
add(rev([(0, DZ1 - 2), (28, DZ1 - 2), (28, 985), (22, 985), (22, SHAFT_END - SPLINE_LEN), (0, SHAFT_END - SPLINE_LEN)], *OUT), "rear_output_shaft", STEEL)
add(helical(30, 1.5, 0.0, SPLINE_LEN, SHAFT_END - SPLINE_LEN, *OUT, hand=1), "rear_output_spline", STEEL)

# ---------------------------------------------------------------- housing, wall radii from FIT_ZS/FIT_RI/FIT_RO
SECTION_C = (0.0, -45.0)
WALL = 5.0
RAIL_LOBES = [(rx, ry, 18.0) for rx, ry in RAILS.values()]


def hull_points(circles, grow=0.0, n=72):
    """Convex hull of circles, resampled at n equal angles about SECTION_C so that lofts stay aligned."""
    pts = []
    for cx, cy, r in circles:
        for j in range(240):
            a = 2 * pi * j / 240
            pts.append((cx + (r + grow) * cos(a), cy + (r + grow) * sin(a)))
    pts = sorted(set(pts))
    cross = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def half(seq):
        out = []
        for p in seq:
            while len(out) > 1 and cross(out[-2], out[-1], p) <= 0:
                out.pop()
            out.append(p)
        return out[:-1]

    hull = half(pts) + half(pts[::-1])
    out = []
    for i in range(n):
        a = 2 * pi * i / n
        d = (cos(a), sin(a))
        best = None
        for k in range(len(hull)):
            p, q = hull[k], hull[(k + 1) % len(hull)]
            e = (q[0] - p[0], q[1] - p[1])
            den = d[0] * e[1] - d[1] * e[0]
            if abs(den) < 1e-12:
                continue
            w = (p[0] - SECTION_C[0], p[1] - SECTION_C[1])
            t = (w[0] * e[1] - w[1] * e[0]) / den
            u = (w[0] * d[1] - w[1] * d[0]) / den
            if t >= 0 and -1e-9 <= u <= 1 + 1e-9 and (best is None or t > best):
                best = t
        out.append((SECTION_C[0] + best * d[0], SECTION_C[1] + best * d[1]))
    return out


def interp(z, zs, vs):
    for k in range(len(zs) - 1):
        if zs[k] <= z <= zs[k + 1]:
            return vs[k] + (vs[k + 1] - vs[k]) * (z - zs[k]) / (zs[k + 1] - zs[k])
    return vs[0] if z < zs[0] else vs[-1]


def floor_ri(z):
    """Smallest wall radius about the input axis that still clears the parts measured from the side view."""
    if z <= 100:
        return 151.0
    if z <= 155:
        return 118.0
    if z <= 250:
        return 56.0
    if z <= 530:
        return 96.0 if 455 <= z <= 495 else 66.0
    return 72.0 if z <= 740 else 50.0 if z <= 760 else 0.1


def floor_ro(z):
    if z < 240:
        return 30.0
    if z <= 735:
        return 80.0
    if z <= 790:
        return 58.0
    if z <= 955:
        return 78.0
    return 49.0


def section(z, solid_lobes=True):
    ri = max(interp(z, FIT_ZS, FIT_RI), floor_ri(z))
    ro_fit = interp(z, FIT_ZS, FIT_RO)
    dxo = interp(z, FIT_ZS, FIT_DXO)
    ro = max(ro_fit, floor_ro(z) + abs(dxo))
    if z <= 70:
        # The bell wall is offset from the shaft axis, so its radius must still clear the flywheel after the offset.
        ri = max(ri, 151.0 + hypot(FP["bell_x"], FP["bell_y"]))
        c = [(FP["bell_x"], FP["bell_y"], ri), (dxo, OUT[1], ro)]
    else:
        c = [(0.0, 0.0, ri), (dxo, OUT[1], ro)]
    if min(FP["fr_z0"], 100.0) <= z <= max(FP["fr_z1"], 820.0):
        c.append((*FRONT, max(FP["fr_r"], 53.0)))
    if 296 <= z <= 740:
        c += RAIL_LOBES
    if solid_lobes:
        if FP["top_z0"] <= z <= FP["top_z1"]:
            c.append((FP["top_x"], FP["top_y"], FP["top_r"]))
        if FP["tail_z0"] <= z <= FP["tail_z1"]:
            c.append((FP["tail_x"], FP["tail_y"], FP["tail_r"]))
        if z <= 14:
            c += [(FP[f"l{i}_x"], FP[f"l{i}_y"], FP[f"l{i}_r"]) for i in range(1, 6)]
    return c


def section_wire(circles, z, grow=0.0):
    return Wire.make_polygon([Vector(x, y, z) for x, y in hull_points(circles, grow)], close=True)


def plate(circles, grow, z0, z1):
    return Pos(0, 0, z0) * extrude(Face(section_wire(circles, 0.0, grow)), z1 - z0, dir=(0, 0, 1))


def pan_prism(x0, x1, yb, ytop, z0, z1, ch):
    """Sump profile in (y, z) with a chamfered rear-bottom edge, extruded along X."""
    prof = [(ytop, z0), (yb, z0), (yb, z1 - ch), (yb + ch, z1), (ytop, z1)]
    wire = Wire.make_polygon([Vector(x0, y, z) for y, z in prof], close=True)
    return extrude(Face(wire), x1 - x0, dir=(1, 0, 0))


def boss_cylinder(i):
    """A radial casting boss, set by angle about the housing centre, axial position, radius and height above the wall."""
    a, zb = radians(FP[f"b{i}_a"]), FP[f"b{i}_z"]
    pts = hull_points(section(zb))
    p = pts[int(round((a % (2 * pi)) / (2 * pi) * 72)) % 72]
    tip = hypot(p[0] - SECTION_C[0], p[1] - SECTION_C[1]) + FP[f"b{i}_h"]
    d = Vector(cos(a), sin(a), 0)
    return Plane(origin=(SECTION_C[0], SECTION_C[1], zb), z_dir=d) * Cylinder(max(FP[f"b{i}_r"], 2.0), tip, align=(Align.CENTER, Align.CENTER, Align.MIN))


if int(P.housing):
    # z = 472 instead of the fitted 470: it keeps a loft facet from meeting the sump wall in a sliver edge.
    LOFT_Z = sorted([472.0 if z == 470.0 else z for z in FIT_ZS] + [955.0])
    outer = Solid.make_loft([section_wire(section(z), z) for z in LOFT_Z], ruled=True)
    cavity_z = [-1.0] + LOFT_Z[1:-1] + [LOFT_Z[-1] + 1.0]
    inner = Solid.make_loft([section_wire(section(max(z, 0.0), False), z, -WALL) for z in cavity_z], ruled=True)
    PX0, PX1, PZ0, PZ1 = FP["pan_x0"], FP["pan_x1"], FP["pan_z0"], FP["pan_z1"]
    BELL_BOLTS = [(FP[f"l{i}_x"], FP[f"l{i}_y"]) for i in range(1, 6) if hypot(FP[f"l{i}_x"], FP[f"l{i}_y"]) >= 158.0]
    JZ = (200.0, 760.0)
    JOINT_SECTION = section(740.0, False)
    JOINTS = {JZ[1]: [hull_points(JOINT_SECTION, 13.0)[int(i * 72 / 14)] for i in range(14)]}

    adds = []
    # Sump around the mechatronics, the front differential and its tube.
    prism = pan_prism(PX0, PX1, PAN_Y, -110.0, PZ0, PZ1, PAN_CH)
    prism_in = pan_prism(PX0 + 5, PX1 - 5, PAN_Y + 5, -100.0, PZ0 + 5, PZ1 - 5, PAN_CH)
    adds.append(prism & box(PX0 - 1, PX1 + 1, PAN_Y + 18, -100.0, PZ0 - 1, PZ1 + 1))
    adds += [boss_cylinder(i) for i in range(8) if FP[f"b{i}_h"] > 0.5]
    adds.append(x_cyl(FP["boss_r"], 30.0, FP["boss_x"], FD_Y, FD_Z))
    adds.append(x_cyl(FP["tube_r"], FP["tube_x"], 30.0, FD_Y, FD_Z))
    adds.append(box(-60.0, FP["boss_x"], FD_Y, -110.0, FD_Z - 40.0, FD_Z + 40.0))
    # Bell ribs, joint flanges, tower and fill bosses.
    for i in range(10):
        a = 2 * pi * i / 10 + pi / 10
        adds.append((Pos(0, 0, 15.0) * Rot(0, 0, degrees(a)) * box(100, 195, -3, 3, 0, 150)) & Solid.make_loft([section_wire(section(z, False), z, 4.0) for z in FIT_ZS[:7]], ruled=True))
    adds.append(plate(JOINT_SECTION, 21.0, JZ[1] - 6.0, JZ[1] + 6.0))
    for zr in (262.0, 345.0, 430.0, 545.0, 620.0, 690.0):
        adds.append(plate(section(zr), 2.5, zr, zr + 4.0))
    body = outer.fuse(*adds)

    sump_cavity = prism_in & box(PX0, PX1, PAN_Y + 17, -99.0, PZ0, PZ1)
    cuts = [inner, sump_cavity,
            x_cyl(FP["boss_r"] - 6.0, 36.0, FP["boss_x"] - 6.0, FD_Y, FD_Z),
            x_cyl(FP["tube_r"] - 5.0, FP["tube_x"] - 1.0, 37.0, FD_Y, FD_Z),
            x_cyl(15.0, FP["boss_x"] - 7.0, FP["boss_x"] + 1.0, FD_Y, FD_Z)]
    cuts += [rod(6.5, -1, 16, x, y) for x, y in BELL_BOLTS]
    cuts += [rod(4.02, JZ[1] - 7.0, JZ[1] + 7.0, x, y) for x, y in JOINTS[JZ[1]]]
    body = body.cut(*cuts)

    rails = [(*r, 8.02) for r in RAILS.values()]

    mid_wall = Solid.make_loft([section_wire(section(max(z, 0.0), False), z, -WALL / 2) for z in cavity_z], ruled=True)

    def web(z0, z1, bores):
        w = mid_wall & box(-340, 340, -440, 340, z0, z1)
        return w.cut(*[rod(br, z0 - 1, z1 + 1, bx, by) for bx, by, br in bores], sump_cavity)

    body = body.fuse(
        web(268.0, 296.0, [(0, 0, 42.02), (*OUT, 45.02), (*FRONT, 18.0), (62, -14, 9.5)] + rails),
        web(497.0, 530.0, [(0, 0, 41.02), (*OUT, 45.02), (*FRONT, 18.0), (*IDLER, 11.02)] + rails),
        web(720.0, 752.0, [(0, 0, 42.02), (*OUT, 42.02), (*FRONT, 34.02)] + rails),
        web(789.0, 814.0, [(*OUT, 40.02), (*FRONT, 17.0)]),
        ring(42.02, 54.0, 960.0, 985.0, *OUT),
    )

    body = body & box(-340, 340, -440, 340, 0.0, 1000.0)
    if int(P.cutaway):
        body = body - box(0, 330, -60, 330, -2, 1000)

    # Close the front differential chamber off from the flywheel: tube wall, chamber shell, end disc and an arc roof.
    tube_shell = x_cyl(FP["tube_r"], FP["tube_x"], 40.0, FD_Y, FD_Z) - x_cyl(FP["tube_r"] - 5.0, FP["tube_x"] - 1.0, 41.0, FD_Y, FD_Z)
    fd_shell = x_cyl(FP["boss_r"], 36.0, FP["boss_x"], FD_Y, FD_Z).cut(
        x_cyl(FP["boss_r"] - 6.0, 35.0, FP["boss_x"] - 6.0, FD_Y, FD_Z), rod(150.0, -40.0, 170.0), rod(24.0, 90.0, 140.0, *FRONT),
        x_cyl(15.0, FP["boss_x"] - 7.0, FP["boss_x"] + 1.0, FD_Y, FD_Z))
    end_disc = x_cyl(FP["boss_r"], 34.0, 38.0, FD_Y, FD_Z).cut(x_cyl(FP["tube_r"] - 5.0, 33.0, 39.0, FD_Y, FD_Z), rod(146.0, -40.0, 170.0), box(-340, 340, -440, 340, -60.0, 0.0))
    arc_roof = (ring(146.0, 150.0, 2.0, 124.0) & box(36.0, 100.0, FD_Y - 90.0, -100.0, 0.0, 126.0)).cut(rod(24.0, 90.0, 140.0, *FRONT))
    body = body.fuse(tube_shell, fd_shell & box(-340, 340, -440, 340, 0.0, 200.0), end_disc, arc_roof)
    bell = body & box(-340, 340, -440, 340, -1.0, JZ[0])
    case = body & box(-340, 340, -440, 340, JZ[0], JZ[1])
    tail = body & box(-340, 340, -440, 340, JZ[1], 1000.0)
    add(bell, "bell_housing", CASE)
    add(case, "gear_case", CASE)
    add(tail, "tail_housing", CASE)

    pan = (prism & box(PX0 - 1, PX1 + 1, PAN_Y - 1, PAN_Y + 18, PZ0 - 1, PZ1 + 1)) - prism_in
    add(pan, "oil_pan", CASE)
    add(y_cyl(9, PAN_Y - 8, PAN_Y, -60, 300), "oil_drain_plug", DARK)
    add(ring(62.5, 66, 533, 720) & box(-50, 50, 25, 100, 525, 725), "oil_drip_pan", BROWN)

    if int(P.bolts):
        for i, (bx, by) in enumerate(BELL_BOLTS):
            add(rod(6.4, 0.5, 12, bx, by), f"bell_dowel_{i}", DARK)
        for i, (bx, by) in enumerate(JOINTS[JZ[1]]):
            add(rod(3.98, JZ[1] - 6.0, JZ[1] + 6.0, bx, by) + rod(7, JZ[1] + 6.0, JZ[1] + 10.0, bx, by), f"case_bolt_{i}", DARK)

result = Compound(children=PARTS)
