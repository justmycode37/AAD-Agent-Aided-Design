"""Build a detailed Seamaster-inspired watch as an offline Python example.

Custom mechanism, not factory Omega 8800 geometry or a proven operating watch.
All dimensions are millimetres. Run --help for output and inspection options.
This orchestration script writes files; it is not a sandbox model.py payload.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from build123d import *


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def validation_failures(checks: dict) -> list[str]:
    """Fail closed on missing, non-finite or failing selected geometric evidence."""
    failures = []
    invalid = checks.get("invalid_parts")
    if not isinstance(invalid, list):
        failures.append("Missing part-validity results")
    elif invalid:
        failures.append(f"Invalid BREP components: {invalid}")

    def check_value(label: str, value: object, minimum: float, maximum: float) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            failures.append(f"{label}: missing or non-finite measurement")
        elif not minimum <= value <= maximum:
            failures.append(f"{label}: {value} outside [{minimum}, {maximum}]")

    tables = [
        ("meshes", "error_mm", -1e-7, 1e-7),
        ("mesh_sweeps", "max_polygon_interference_area_mm2", 0.0, 1e-7),
        ("mesh_sweeps", "transverse_contact_ratio", 1.0, math.inf),
        ("exact_intersections", "intersection_mm3", 0.0, 1e-7),
        ("bracelet_articulation_sweep", "max_intersection_mm3", 0.0, 1e-7),
    ]
    for table, field, low, high in tables:
        rows = checks.get(table)
        if not isinstance(rows, list) or not rows:
            failures.append(f"Missing {table} results")
            continue
        for index, row in enumerate(rows):
            check_value(
                f"{table}[{index}].{field}",
                row.get(field) if isinstance(row, dict) else None,
                low,
                high,
            )
    return failures


def make_colored_scene(ctx, output_dir: Path, colors: dict[str, str]):
    """Scope the tessellator color adapter to this single-threaded example build.

    The current scene writer supplies a uniform color. Override that adapter
    only while make_scene runs and restore it even if meshing raises.
    """
    import ocp_tessellate.convert as convert
    from aad_render import make_scene

    original = convert.to_ocpgroup

    def colored(*args, **kwargs):
        kwargs["colors"] = [
            colors.get(name.split("__")[0], "#929ba5")
            for name in kwargs.get("names", [])
        ]
        return original(*args, **kwargs)

    with patch.object(convert, "to_ocpgroup", colored):
        return make_scene(ctx, output_dir)


def prepare_views(groups: dict, scene_parts: list[dict]) -> list[dict]:
    """Resolve independent inspection recipes against the saved scene leaves."""
    # Group references in view recipes resolve against the saved leaf manifest below.
    views = [
        {
            "id": "dial",
            "label": "Dial and case",
            "exclude": ["Sapphire crystal"],
            "view": "top",
            "groups": [
                "case",
                "crown",
                "dial",
                "hands",
                "bezel markings",
                "crystal",
                "seals",
            ],
        },
        {
            "id": "train",
            "label": "Going train and escapement",
            "view": "bottom",
            "groups": [
                "going train",
                "barrel",
                "springs",
                "escapement",
                "oscillator",
                "bearings",
            ],
        },
        {
            "id": "spring",
            "label": "Mainspring and barrel",
            "view": "top",
            "groups": ["springs", "barrel"],
            "exclude": ["Mainspring barrel cup", "Barrel removable lid"],
        },
        {
            "id": "balance",
            "label": "Balance and escapement",
            "view": "bottom",
            "groups": ["oscillator", "escapement"],
        },
        {
            "id": "hands",
            "label": "Motion works and calendar",
            "view": "top",
            "groups": ["motion works", "calendar", "keyless works"],
        },
        {
            "id": "automatic",
            "label": "Automatic winding",
            "view": "bottom",
            "groups": ["automatic", "barrel"],
        },
        {
            "id": "bracelet",
            "label": "Bracelet and clasp",
            "view": "iso",
            "groups": [
                "bracelet structure",
                "bracelet finish",
                "bracelet pins",
                "clasp",
            ],
        },
        {
            "id": "clasp",
            "label": "Deployant clasp",
            "view": "bottom",
            "groups": ["clasp"],
        },
        {
            "id": "section",
            "label": "Section through complete watch",
            "view": "right",
            "presentation": {
                "section": {"planes": [{"plane": "YZ", "offset": 0}]},
                "style": "shaded",
            },
        },
        {
            "id": "rear",
            "label": "Exhibition caseback",
            "view": "bottom",
            "presentation": {
                "hide": ["Sapphire crystal", "Caseback sapphire"],
                "style": "shaded",
            },
        },
        {
            "id": "all",
            "label": "Complete wrist pose",
            "view": "iso",
            "presentation": {"style": "shaded"},
        },
    ]
    raw = {"parts": scene_parts}
    for v in views:
        if "groups" in v:
            names = [
                n
                for g in v.pop("groups")
                for n in groups.get(g, [])
                if n not in v.get("exclude", [])
            ]
            v.pop("exclude", None)
            names = [
                x["path"]
                for x in raw["parts"]
                if any(x["name"] == n or x["name"].startswith(n + "__") for n in names)
            ]
            v["presentation"] = {"isolate": names, "style": "shaded"}
    return views


def build_watch(output_dir: Path) -> dict:
    from aad_platform.viewer.bundle import export_bundle
    from shapely.geometry import Polygon as SPolygon
    from shapely.affinity import rotate as srotate, translate as stranslate

    OUT = output_dir.resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    parts = []
    colors = {}
    groups = {}
    STEEL = "#b8c3ce"
    POLISH = "#e3e8ed"
    BLUE = "#07346a"
    DARK = "#071c35"
    LUME = "#e0efda"
    GOLD = "#b89b52"
    RED = "#db313c"

    def add(name, shape, color=STEEL, group="case"):
        shape.label = name
        shape.color = Color(color)
        parts.append(shape)
        colors[name] = color
        groups.setdefault(group, []).append(name)
        return shape

    def cyl(r, h, z, x=0, y=0):
        return Pos(x, y, z) * Cylinder(r, h)

    def ring(ro, ri, h, z):
        return cyl(ro, h, z) - cyl(ri, h + 1, z)

    def rounded(x, y, z, r):
        b = Box(x, y, z)
        return fillet(b.edges().filter_by(Axis.Z), r)

    def text(s, size, x, y, z, color=POLISH, name=None):
        return add(
            name or "Dial inscription " + s,
            Pos(x, y, z) * extrude(Text(s, font_size=size, font="Arial"), amount=0.025),
            color,
            "dial",
        )

    def radial(shape, a):
        return Rot(0, 0, -a) * shape

    # midcase: stepped seat, inner cavity, stem bore
    case = ring(20.5, 16.0, 7.5, 0)
    case -= cyl(17.8, 2.0, 3.3)
    case -= cyl(17.3, 1.6, -3.5)
    case -= Pos(19, 0, -0.8) * Rot(0, 90, 0) * Cylinder(0.8, 10)
    # lug arms and real spring-bar bores
    for sy in (-1, 1):
        for sx in (-1, 1):
            lug = (
                Pos(sx * 12, sy * 20.0, -0.4)
                * Rot(sy * 10, 0, sx * sy * 8)
                * rounded(4.4, 10, 6.0, 1.4)
            )
            lug -= Pos(sx * 12, sy * 23.2, -0.6) * Rot(0, 90, 0) * Cylinder(0.65, 8)
            case += lug
    add("Midcase and drilled lyre lugs", case)
    for sy in (-1, 1):
        add(
            "Spring bar " + str(sy),
            Pos(0, sy * 23.2, -0.6) * Rot(0, 90, 0) * Cylinder(0.6, 25),
            POLISH,
            "bracelet",
        )
    # bezel continuous scalloped perimeter and ceramic insert
    bezel = ring(21, 17.9, 1.8, 4.2)
    for a in range(0, 360, 30):
        x, y = 22.3 * math.sin(math.radians(a)), 22.3 * math.cos(math.radians(a))
        bezel -= cyl(2.0, 3, 4.2, x, y)
    add("Scalloped rotating bezel", bezel, POLISH)
    add("Blue ceramic bezel insert", ring(20.35, 18.05, 0.45, 5.18), BLUE)
    for i in range(60):
        if i % 5:
            add(
                "Bezel minute %02d" % i,
                radial(Pos(0, 19.5, 5.43) * Box(0.13, 0.64, 0.035), i * 6),
                POLISH,
                "bezel markings",
            )
    for i in range(10, 60, 10):
        a = math.radians(i * 6)
        add(
            "Bezel numeral " + str(i),
            Pos(19.2 * math.sin(a), 19.2 * math.cos(a), 5.44)
            * Rot(0, 0, -i * 6)
            * extrude(Text(str(i), font_size=1.6, font="Arial"), amount=0.035),
            POLISH,
            "bezel markings",
        )
    tri = extrude(
        make_face(Polygon((-0.7, 19.9), (0.7, 19.9), (0, 18.6), align=None)),
        amount=0.04,
    )
    add("Bezel zero triangle", Pos(0, 0, 5.43) * tri, POLISH, "bezel markings")
    add("Bezel luminous pip", cyl(0.25, 0.05, 5.49, 0, 19.4), LUME, "bezel markings")
    # crystal is a real removable component; hidden by default for unimpeded dial review
    add("Sapphire crystal", cyl(17.75, 0.8, 4.9), "#9fcbe4", "crystal")
    add("Crystal perimeter gasket", ring(17.98, 17.76, 0.65, 4.75), "#26303a", "seals")
    add("Caseback gasket", ring(16.95, 16.65, 0.5, -3.65), "#26303a", "seals")
    add("Exhibition caseback steel rim", ring(17.25, 13.0, 1.0, -4.2), STEEL)
    add("Caseback sapphire", cyl(12.95, 0.55, -4.25), "#9fcbe4", "back glass")
    # dial plate with central arbor bore and six o'clock date aperture
    dial = cyl(16.7, 0.45, 2.0) - cyl(0.65, 1, 2) - Pos(0, -10.6, 2) * Box(2.6, 1.8, 1)
    # machine sinusoidal grooves into actual face
    for k in range(-7, 8):
        pts = []
        for n in range(33):
            x = -17 + n * 34 / 32
            y = k * 1.9 + 0.26 * math.sin(x * 1.05)
            pts.append((x, y, 2.20))
        path = Spline(*pts)
        tool = sweep(Plane(origin=pts[0], z_dir=path % 0) * Circle(0.065), path=path)
        dial -= tool
    add("Blue wave engraved dial", dial, BLUE, "dial")
    add("Date wheel visible plate", ring(12.1, 9.0, 0.14, 1.5), POLISH, "movement")
    text("5", 1.35, 0, -10.6, 1.58, DARK, "Date numeral")
    for i in range(60):
        a = i * 6
        add(
            "Dial minute track %02d" % i,
            radial(Pos(0, 15.85, 2.24) * Box(0.1, 0.48, 0.04), a),
            POLISH,
            "dial",
        )
    for i in range(12):
        if i == 6:
            continue
        a = i * 30
        if i == 0:
            for x in (-0.62, 0.62):
                add(
                    "Twelve marker " + str(x),
                    Pos(x, 13.6, 2.36) * rounded(0.88, 2.75, 0.22, 0.2),
                    POLISH,
                    "dial",
                )
                add(
                    "Twelve lume " + str(x),
                    Pos(x, 13.6, 2.49) * Box(0.5, 2.3, 0.04),
                    LUME,
                    "dial",
                )
        elif i in (3, 9):
            add(
                "Hour baton " + str(i),
                radial(Pos(0, 13.75, 2.36) * rounded(1.05, 2.8, 0.22, 0.3), a),
                POLISH,
                "dial",
            )
            add(
                "Baton lume " + str(i),
                radial(Pos(0, 13.75, 2.49) * Box(0.65, 2.35, 0.04), a),
                LUME,
                "dial",
            )
        else:
            x, y = 13.7 * math.sin(math.radians(a)), 13.7 * math.cos(math.radians(a))
            add(
                "Hour applied surround " + str(i),
                cyl(0.9, 0.22, 2.36, x, y),
                POLISH,
                "dial",
            )
            add(
                "Hour luminous dot " + str(i), cyl(0.65, 0.04, 2.49, x, y), LUME, "dial"
            )
    text("Ω", 2.5, 0, 9.3, 2.25)
    text("OMEGA", 1.35, 0, 7.1, 2.25)
    text("Seamaster", 1.25, 0, 5.35, 2.25, RED)
    text("CO-AXIAL", 0.85, 0, -4.7, 2.25)
    text("MASTER CHRONOMETER", 0.67, 0, -6.0, 2.25)
    text("300m / 1000ft", 0.8, 0, -7.25, 2.25)
    # skeleton hands posed 10:10; clearance checked below
    for name, length, width, z, a in [
        ("Hour skeleton hand", 9.2, 1.35, 2.85, -55),
        ("Minute skeleton hand", 12.5, 1.05, 3.35, 60),
    ]:
        body = Pos(0, length / 2 - 0.6, 0) * rounded(width, length + 1.2, 0.16, 0.35)
        body -= Pos(0, length * 0.53, 0) * Box(width * 0.52, length * 0.5, 1)
        body += Cylinder(0.95, 0.16)
        body -= Cylinder(0.27, 1)
        add(name, Pos(0, 0, z) * radial(body, a), POLISH, "hands")
        add(
            name + " luminous tip",
            Pos(0, 0, z + 0.1)
            * radial(Pos(0, length - 0.75, 0) * Box(width * 0.60, 1.0, 0.025), a),
            LUME,
            "hands",
        )
    sec = (
        Pos(0, 4.4, 0) * Box(0.13, 18, 0.12)
        + Pos(0, 10.5, 0) * Cylinder(0.55, 0.12)
        + Cylinder(0.6, 0.12)
    )
    add("Red seconds hand", Pos(0, 0, 3.85) * radial(sec, 192), RED, "hands")
    add(
        "Seconds luminous dot",
        Pos(0, 0, 3.93) * radial(Pos(0, 10.5, 0) * Cylinder(0.33, 0.025), 192),
        LUME,
        "hands",
    )
    STEEL = "#929ba5"
    POLISH = "#cbd1d8"
    lookup = lambda n: next(p for p in parts if p.label == n)

    def remove(name):
        nonlocal parts
        parts = [p for p in parts if p.label != name]
        for g in groups.values():
            if name in g:
                g.remove(name)

    def replace(name, shape):
        i = next(i for i, p in enumerate(parts) if p.label == name)
        shape.label = name
        shape.color = Color(colors[name])
        parts[i] = shape

    # Stepped dial seat and actual lug bores; deepen lower service cavity.
    case = (lookup("Midcase and drilled lyre lugs") & Box(60, 49.9, 20)) - cyl(
        17.1, 2.0, 2.0
    )
    case -= cyl(17.85, 2.5, -3.35)
    case -= Pos(19, 0, -0.8) * Rot(0, 90, 0) * Cylinder(0.8, 10)
    replace("Midcase and drilled lyre lugs", case)
    remove("Spring bar -1")
    remove("Spring bar 1")
    remove("Date wheel visible plate")
    replace("Exhibition caseback steel rim", ring(17.25, 13.0, 2.23, -5.665))
    replace("Caseback sapphire", cyl(12.95, 0.65, -6.13))
    R = (17.75**2 + 0.68**2) / (2 * 0.68)
    replace("Sapphire crystal", cyl(17.75, 3.0, 6.0) & Pos(0, 0, 6.78 - R) * Sphere(R))
    replace("Caseback gasket", ring(16.95, 16.65, 0.5, -4.55))
    add("Caseback threaded skirt", ring(17.05, 15.95, 2.30, -3.40), STEEL, "case")
    # Open-bores for all three nested hand drives, with press-fit interfaces declared separately.
    for n, r in [("Hour skeleton hand", 0.515), ("Minute skeleton hand", 0.295)]:
        replace(n, lookup(n) - cyl(r, 1, 3.1))
    replace("Red seconds hand", lookup("Red seconds hand") - cyl(0.12, 0.7, 3.85))
    # Add seconds tube, cannon pinion tube, hour pipe. Actual air gaps between concentric parts.
    add("Seconds staff solid 0.24", cyl(0.12, 6.65, 0.525), POLISH, "motion works")
    add(
        "Minute cannon tube ID0.30 OD0.56",
        ring(0.28, 0.15, 4.7, 1.05),
        POLISH,
        "motion works",
    )
    add("Hour pipe ID0.64 OD1.00", ring(0.50, 0.32, 1.95, 1.9), GOLD, "motion works")
    # True involute flanks as interpolating splines; circular tip and root lands.
    gear_specs = []
    gear2d = {}
    gears = {}
    meshes = []
    fitted = []
    involute_cache = {}

    def inv(r, rb):
        a = math.acos(min(1, rb / r))
        return math.tan(a) - a

    def profile(n, m, bore=0.2, backlash=0.012):
        key = (n, round(m, 9), bore, backlash)
        if key in involute_cache:
            return involute_cache[key]
        rp = n * m / 2
        rb = rp * math.cos(math.pi / 6)
        rf = rp - 1.25 * m
        ra = rp + m
        rs = max(rf, rb)
        half = math.pi / (2 * n) - backlash / (4 * rp)
        ip = inv(rp, rb)

        def flank(r, sign, a):
            ph = a + sign * (half + ip - inv(r, rb))
            return (r * math.cos(ph), r * math.sin(ph), 0)

        edges = []
        poly = []
        for i in range(n):
            a = i * math.tau / n
            l = [flank(rs + (ra - rs) * j / 12, -1, a) for j in range(13)]
            r = [flank(ra - (ra - rs) * j / 12, 1, a) for j in range(13)]
            al = math.atan2(l[0][1], l[0][0])
            ar = math.atan2(r[-1][1], r[-1][0])
            # angles unwrapped around the current tooth
            al = a - (half + ip - inv(rs, rb))
            ar = a + (half + ip - inv(rs, rb))
            root_l = (rf * math.cos(al), rf * math.sin(al), 0)
            root_r = (rf * math.cos(ar), rf * math.sin(ar), 0)
            if rs > rf + 1e-8:
                edges.append(Edge.make_line(root_l, l[0]))
            edges.append(Edge.make_spline(l))
            tip_l = a - (half + ip - inv(ra, rb))
            tip_r = a + (half + ip - inv(ra, rb))
            edges.append(
                Edge.make_circle(
                    ra,
                    Plane.XY,
                    start_angle=math.degrees(tip_l),
                    end_angle=math.degrees(tip_r),
                )
            )
            edges.append(Edge.make_spline(r))
            if rs > rf + 1e-8:
                edges.append(Edge.make_line(r[-1], root_r))
            end = (i + 1) * math.tau / n - (half + ip - inv(rs, rb))
            edges.append(
                Edge.make_circle(
                    rf,
                    Plane.XY,
                    start_angle=math.degrees(ar),
                    end_angle=math.degrees(end),
                )
            )
            poly.append(root_l[:2])
            poly.extend(q[:2] for q in l)
            poly.extend(
                (
                    ra * math.cos(tip_l + (tip_r - tip_l) * j / 8),
                    ra * math.sin(tip_l + (tip_r - tip_l) * j / 8),
                )
                for j in range(1, 9)
            )
            poly.extend(q[:2] for q in r)
            poly.append(root_r[:2])
            poly.extend(
                (
                    rf * math.cos(ar + (end - ar) * j / 5),
                    rf * math.sin(ar + (end - ar) * j / 5),
                )
                for j in range(1, 6)
            )
        face = Face(Wire(edges))
        poly = SPolygon(poly)
        involute_cache[key] = (face, poly)
        return face, poly

    def gear(
        name,
        n,
        m,
        xy,
        z,
        th=0.18,
        bore=0.22,
        phase=0,
        spokes=0,
        color=GOLD,
        group="going train",
    ):
        face, poly = profile(n, m, bore)
        body = extrude(face, amount=th)
        body -= Pos(0, 0, th / 2) * Cylinder(bore, th + 1)
        rp = n * m / 2
        if spokes and rp > 1.1:
            for j in range(spokes):
                a = j * math.tau / spokes
                body -= Pos(
                    rp * 0.57 * math.cos(a), rp * 0.57 * math.sin(a), th / 2
                ) * Cylinder(rp * 0.19, th + 1)
        shape = Pos(xy[0], xy[1], z - th / 2) * Rot(0, 0, phase) * body
        add(name, shape, color, group)
        gear2d[name] = poly
        gears[name] = shape
        gear_specs.append(
            dict(
                name=name,
                teeth=n,
                module_mm=m,
                pressure_angle_deg=30,
                center=list(xy),
                z=z,
                width=th,
                bore_radius=bore,
                phase_deg=phase,
                backlash_pair_mm=0.012,
            )
        )
        return shape

    def spec(n):
        return next(g for g in gear_specs if g["name"] == n)

    def mesh(a, b):
        A, B = spec(a), spec(b)
        dx = B["center"][0] - A["center"][0]
        dy = B["center"][1] - A["center"][1]
        d = math.hypot(dx, dy)
        nom = A["module_mm"] * (A["teeth"] + B["teeth"]) / 2
        meshes.append(
            dict(
                driver=a,
                driven=b,
                actual_distance_mm=d,
                nominal_distance_mm=nom,
                error_mm=d - nom,
                ratio=A["teeth"] / B["teeth"],
            )
        )

    def phase_b(a, bxy, nb):
        A = spec(a)
        phi = math.atan2(bxy[1] - A["center"][1], bxy[0] - A["center"][0])
        return (
            math.degrees(
                phi
                + math.pi
                + math.pi / nb
                - (A["teeth"] / nb) * (math.radians(A["phase_deg"]) - phi)
            )
            % 360
        )

    def mate(name, n, m, xy, z, driver, **kw):
        gear(name, n, m, xy, z, phase=phase_b(driver, xy, n), **kw)
        mesh(driver, name)

    def bearing(name, x, y, z, outer=0.52, bore=0.14, h=0.22):
        add(
            name + " pierced ruby",
            Pos(x, y, 0) * ring(outer, bore, h, z),
            "#ab1f55",
            "bearings",
        )
        add(
            name + " pressed chaton",
            Pos(x, y, 0) * ring(outer + 0.17, outer + 0.015, h + 0.04, z),
            GOLD,
            "bearings",
        )
        fitted.append(
            {
                "interface": name,
                "shaft_radius": bore - 0.02,
                "bearing_bore_radius": bore,
                "radial_clearance_mm": 0.02,
            }
        )

    def screw(name, x, y, z, r=0.36, length=0.9):
        sh = cyl(r * 0.65, length, z - length / 2, x, y) + cyl(r, 0.18, z + 0.09, x, y)
        sh -= Pos(x, y, z + 0.19) * Box(r * 1.6, 0.10, 0.15)
        add(name, sh, POLISH, "fasteners")

    # Layout and exact pair distances, separate axial gear planes.
    C = (0, 0)
    B = (-6, 4)
    T = (4.2, math.sqrt(4.5**2 - 4.2**2))
    F = (T[0], T[1] - 3.4)
    E = (F[0], F[1] - 3.38)
    BAL = (-3.8, -8.1)
    mbar = 2 * math.hypot(*B) / 108
    gear(
        "Barrel external wheel 96",
        96,
        mbar,
        B,
        -0.8,
        th=0.24,
        bore=0.57,
        group="barrel",
    )
    mate(
        "Center driving pinion 12",
        12,
        mbar,
        C,
        -0.8,
        "Barrel external wheel 96",
        bore=0.31,
    )
    gear("Center minute wheel 80", 80, 0.10, C, -1.30, bore=0.31, spokes=5)
    mate("Third pinion 10", 10, 0.10, T, -1.30, "Center minute wheel 80", bore=0.16)
    gear("Third wheel 75", 75, 0.08, T, -0.70, bore=0.16, spokes=5)
    mate("Fourth pinion 10", 10, 0.08, F, -0.70, "Third wheel 75", bore=0.16)
    gear("Fourth seconds wheel 96", 96, 0.065, F, -0.25, bore=0.16, spokes=5)
    mate(
        "Escape pinion 8",
        8,
        0.065,
        E,
        -0.25,
        "Fourth seconds wheel 96",
        th=0.15,
        bore=0.13,
    )
    # Separate central-seconds return, two reversals retain clockwise rotation.
    d = math.hypot(*F)
    mid = (F[0] / 2, F[1] / 2)
    off = math.sqrt(2.8**2 - (d / 2) ** 2)
    I = (mid[0] - F[1] / d * off, mid[1] + F[0] / d * off)
    gear("Fourth seconds transfer 32", 32, 0.1, F, -1.59, th=0.13, bore=0.16)
    mate(
        "Seconds idler 24",
        24,
        0.1,
        I,
        -1.59,
        "Fourth seconds transfer 32",
        th=0.13,
        bore=0.14,
    )
    mate(
        "Central seconds return 32",
        32,
        0.1,
        C,
        -1.59,
        "Seconds idler 24",
        th=0.13,
        bore=0.12,
    )
    # Barrel cup with hollow interior, actual spiral and slotted arbor.
    add(
        "Mainspring barrel cup",
        Pos(B[0], B[1], 0)
        * (ring(6.28, 6.03, 1.10, -0.08) + cyl(6.28, 0.16, 0.55) - cyl(0.6, 0.5, 0.55)),
        GOLD,
        "barrel",
    )
    add(
        "Barrel removable lid",
        Pos(*B, 0) * (ring(6.02, 0.61, 0.12, -0.61)),
        GOLD,
        "barrel",
    )
    add(
        "Barrel arbor hooked hub",
        cyl(0.55, 4.3, -0.95, *B) - Pos(B[0] + 0.5, B[1], -0.1) * Box(0.2, 0.3, 1.1),
        POLISH,
        "barrel",
    )
    turns = 18
    pts = []
    pts2 = []
    for i in range(1001):
        a = i / 1000 * math.tau * turns
        r = 0.93 + 4.75 * i / 1000
        pts.append((B[0] + r * math.cos(a), B[1] + r * math.sin(a), 0))
        pts2.append(
            (B[0] + (r + 0.10) * math.cos(a), B[1] + (r + 0.10) * math.sin(a), 0)
        )
    spwire = Wire(
        [
            Edge.make_spline(pts),
            Edge.make_line(pts[-1], pts2[-1]),
            Edge.make_spline(list(reversed(pts2))),
            Edge.make_line(pts2[0], pts[0]),
        ]
    )
    add(
        "Mainspring continuous 18-turn ribbon",
        Pos(0, 0, -0.46) * extrude(Face(spwire), amount=0.8),
        "#6a7b8d",
        "springs",
    )
    add(
        "Barrel inner spring hook",
        Pos(B[0] + 0.7, B[1], -0.05) * Box(0.45, 0.25, 0.6),
        POLISH,
        "barrel",
    )
    add(
        "Barrel outer slipping bridle",
        Pos(B[0] + 5.84, B[1], -0.05) * Box(0.2, 2.0, 0.65),
        POLISH,
        "barrel",
    )
    # Arbors and two-sided jewel supports. Nested center arbors are already built above.
    for name, xy, r in [
        ("Third", T, 0.14),
        ("Fourth", F, 0.14),
        ("Escape", E, 0.11),
        ("Seconds idler", I, 0.12),
    ]:
        add(
            name + " stepped arbor",
            cyl(
                r,
                1.10 if name == "Seconds idler" else 3.43,
                -2.0 if name == "Seconds idler" else -0.955,
                *xy,
            ),
            POLISH,
            "going train",
        )
        for z in (-1.95, -2.52) if name == "Seconds idler" else (0.63, -2.52):
            bearing(
                name + (" upper" if z > 0 else " lower"),
                *xy,
                z,
                outer=0.4,
                bore=r + 0.02,
            )
        bearing(name + " endstone", *xy, -2.76, outer=0.32, bore=0.02, h=0.12)
    add(
        "Seconds idler lower cantilever post",
        Pos(I[0], I[1], 0) * ring(0.57, 0.42, 0.8, -2.25),
        STEEL,
        "plates",
    )

    # 15-tooth escape wheel and jewel pallet lever, explicit regulator architecture.
    def escape_wheel():
        p = []
        for i in range(15):
            a = i * math.tau / 15
            for da, r in [
                (0, 1.40),
                (0.06, 2.02),
                (0.115, 2.06),
                (0.17, 1.50),
                (0.40, 1.40),
            ]:
                p.append((E[0] + r * math.cos(a + da), E[1] + r * math.sin(a + da)))
        s = Pos(0, 0, -2.31) * extrude(make_face(Polygon(*p, align=None)), amount=0.12)
        s -= cyl(0.13, 0.6, -2.2, *E)
        for a in (0, 120, 240):
            s -= cyl(
                0.26,
                0.5,
                -2.2,
                E[0] + 0.85 * math.cos(math.radians(a)),
                E[1] + 0.85 * math.sin(math.radians(a)),
            )
        return s

    add("Fifteen tooth lever escape wheel", escape_wheel(), POLISH, "escapement")
    PAL = (1.25, -6.70)
    lever = Pos(*PAL, -2.22) * Cylinder(0.45, 0.14)
    for end in [(E[0] - 1.3, E[1] - 0.45), (E[0] + 0.15, E[1] - 1.65), (-3.2, -8.1)]:
        dx, dy = end[0] - PAL[0], end[1] - PAL[1]
        L = math.hypot(dx, dy)
        lever += (
            Pos((end[0] + PAL[0]) / 2, (end[1] + PAL[1]) / 2, -2.22)
            * Rot(0, 0, math.degrees(math.atan2(dy, dx)) - 90)
            * Box(0.26, L, 0.14)
        )
    lever -= cyl(0.12, 1, -2.2, *PAL)
    add("Pallet lever with fork and guard", lever, POLISH, "escapement")
    for j, xy in enumerate([(E[0] - 1.30, E[1] - 0.45), (E[0] + 0.15, E[1] - 1.65)]):
        add(
            "Pallet impulse jewel " + str(j),
            Pos(*xy, -2.22) * Rot(0, 0, -25) * Box(0.35, 0.65, 0.18),
            "#b12657",
            "escapement",
        )
    for y in (-8.3, -7.9):
        add(
            "Fork horn " + str(y),
            Pos(-3.2, y, -2.22) * Box(0.8, 0.14, 0.14),
            POLISH,
            "escapement",
        )
    add("Pallet staff", cyl(0.10, 3.0, -0.85, *PAL), POLISH, "escapement")
    for z in (0.63, -2.52):
        bearing("Pallet bearing " + str(z), *PAL, z, outer=0.32, bore=0.12)
    # Balance wheel, rim, arms, timing screws, roller jewel, planar hairspring.
    bal = Pos(*BAL, 0) * ring(3.25, 2.88, 0.24, -1.55)
    for a in (0, 120, 240):
        bal += Pos(*BAL, -1.55) * Rot(0, 0, a) * Pos(1.5, 0, 0) * Box(3, 0.22, 0.24)
    bal -= cyl(0.11, 1, -1.5, *BAL)
    add("Free balance wheel with three spokes", bal, GOLD, "oscillator")
    for j in range(8):
        a = j * math.tau / 8
        add(
            "Balance timing screw %02d" % j,
            cyl(0.22, 0.25, -1.31, BAL[0] + 3 * math.cos(a), BAL[1] + 3 * math.sin(a)),
            POLISH,
            "oscillator",
        )
    add("Balance staff", cyl(0.09, 3.2, -0.85, *BAL), POLISH, "oscillator")
    add("Balance impulse roller", cyl(0.55, 0.16, -2.18, *BAL), POLISH, "oscillator")
    add(
        "Roller impulse ruby",
        cyl(0.10, 0.25, -2.25, BAL[0] + 0.48, BAL[1]),
        "#b12657",
        "oscillator",
    )
    for z in (0.63, -2.52):
        bearing("Balance shock bearing " + str(z), *BAL, z, outer=0.5, bore=0.11)
    # First-order oscillator sizing from actual CAD inertia, with explicit material assumptions.
    balance_inertia = 0.0
    for obj in parts:
        if obj.label in groups["oscillator"]:
            density = (
                8.5e-6
                if obj.label.startswith("Free balance")
                else (3.98e-6 if "ruby" in obj.label.lower() else 7.8e-6)
            )
            center = obj.center(CenterOf.MASS)
            offset2 = (center.X - BAL[0]) ** 2 + (center.Y - BAL[1]) ** 2
            balance_inertia += (
                (obj.matrix_of_inertia[2][2] + obj.volume * offset2) * density * 1e-6
            )
    hair_length = math.tau * 10 * (0.45 + 2.6) / 2
    hair_E = 160000.0  # N/mm2, assumed spring alloy; requires material confirmation
    hair_k = balance_inertia * (math.tau * 3) ** 2 * 1000  # Nmm/rad
    hair_thickness = (12 * hair_length * hair_k / (hair_E * 0.10)) ** (1 / 3)
    # Continuous 10-turn hairspring, sized analytically for nominal 3 Hz.

    q = []
    q2 = []
    for i in range(701):
        a = i / 700 * math.tau * 10
        r = 0.45 + 2.15 * i / 700
        q.append((BAL[0] + r * math.cos(a), BAL[1] + r * math.sin(a), 0))
        q2.append(
            (
                BAL[0] + (r + hair_thickness) * math.cos(a),
                BAL[1] + (r + hair_thickness) * math.sin(a),
                0,
            )
        )
    w = Wire(
        [
            Edge.make_spline(q),
            Edge.make_line(q[-1], q2[-1]),
            Edge.make_spline(q2[::-1]),
            Edge.make_line(q2[0], q[0]),
        ]
    )
    add(
        "Balance hairspring continuous 10 turns",
        Pos(0, 0, -0.90) * extrude(Face(w), amount=0.10),
        "#457bb7",
        "oscillator",
    )
    add(
        "Hairspring collet split",
        Pos(*BAL, 0)
        * (ring(0.4, 0.12, 0.15, -0.86) - Pos(0.35, 0, -0.86) * Box(0.3, 0.08, 0.5)),
        POLISH,
        "oscillator",
    )
    add(
        "Hairspring outer stud",
        cyl(0.16, 0.35, -0.75, BAL[0] + 2.62, BAL[1]),
        POLISH,
        "oscillator",
    )
    # Motion works: 12/36 and 10/40 at the same 2.4 mm center distance.
    gear("Cannon pinion 12", 12, 0.1, C, 1.28, th=0.16, bore=0.15, group="motion works")
    mate(
        "Minute intermediate wheel 36",
        36,
        0.1,
        (2.4, 0),
        1.28,
        "Cannon pinion 12",
        th=0.16,
        bore=0.14,
        group="motion works",
    )
    gear(
        "Minute intermediate pinion 10",
        10,
        0.096,
        (2.4, 0),
        1.52,
        th=0.14,
        bore=0.14,
        group="motion works",
    )
    mate(
        "Hour wheel 40",
        40,
        0.096,
        C,
        1.52,
        "Minute intermediate pinion 10",
        th=0.14,
        bore=0.32,
        group="motion works",
    )
    add(
        "Center driving hollow arbor",
        ring(0.31, 0.285, 2.0, -0.65),
        POLISH,
        "going train",
    )
    add("Minute wheel post", cyl(0.12, 0.75, 1.22, 2.4, 0), POLISH, "motion works")
    add(
        "Cannon friction spring split sleeve",
        ring(0.32, 0.285, 0.60, -0.02) - Pos(0.31, 0, -0.02) * Box(0.12, 0.08, 1),
        POLISH,
        "motion works",
    )
    # Calendar has a real 31-position ring and 24-hour drive, date jumper and reset path.
    datering = ring(12.25, 9.3, 0.16, 1.43)
    for i in range(31):
        a = i * math.tau / 31
        datering += (
            Pos(9.2 * math.cos(a), 9.2 * math.sin(a), 1.43)
            * Rot(0, 0, math.degrees(a))
            * Box(0.45, 0.5, 0.16)
        )
    add("Calendar 31 position internally notched ring", datering, POLISH, "calendar")
    for i in range(31):
        # date 5 at six o'clock, the other markings hidden under dial
        a = math.pi + (i - 4) * math.tau / 31
        add(
            "Calendar printed date %02d" % (i + 1),
            Pos(10.65 * math.sin(a), 10.65 * math.cos(a), 1.52)
            * Rot(0, 0, -math.degrees(a) + 180)
            * extrude(Text(str(i + 1), font_size=0.95, font="Arial"), amount=0.012),
            DARK,
            "calendar",
        )
    remove("Date numeral")
    gear(
        "Hour calendar drive 40",
        40,
        0.08,
        C,
        1.02,
        th=0.12,
        bore=0.32,
        group="calendar",
    )
    mate(
        "Daily calendar wheel 80",
        80,
        0.08,
        (0, 4.8),
        1.02,
        "Hour calendar drive 40",
        th=0.12,
        bore=0.14,
        spokes=4,
        group="calendar",
    )
    add(
        "Calendar advancing sprung finger",
        Pos(0, 7, 1.42) * Box(0.20, 4.4, 0.12) + cyl(0.3, 0.12, 1.42, 0, 4.8),
        POLISH,
        "calendar",
    )
    add(
        "Calendar jumper leaf",
        Pos(-9.1, -1, 1.42) * Rot(0, 0, 15) * Box(0.12, 4, 0.12),
        POLISH,
        "calendar",
    )
    add("Calendar jumper roller", cyl(0.23, 0.18, 1.43, -8.65, 0.9), POLISH, "calendar")
    for a in (30, 150, 270):
        x, y = 12.6 * math.cos(math.radians(a)), 12.6 * math.sin(math.radians(a))
        add(
            "Calendar retaining clip " + str(a),
            Pos(x, y, 1.60) * Rot(0, 0, a) * Box(1.2, 2.0, 0.16),
            STEEL,
            "calendar",
        )
        screw("Date clip screw " + str(a), x, y, 1.66, r=0.25, length=0.65)
    print("Train, barrel, oscillator and calendar created", len(parts), flush=True)
    # Winding / setting: crown, slidable square stem, clutch, yoke, setting wheels.
    add(
        "Stem square section and pivot",
        Pos(14.4, 0, -0.8) * Box(5.8, 0.48, 0.48)
        + Pos(20.4, 0, -0.8) * Rot(0, 90, 0) * Cylinder(0.30, 6.4),
        POLISH,
        "keyless works",
    )
    add(
        "Stem sliding clutch grooved",
        Pos(12.8, 0, -0.8)
        * Rot(0, 90, 0)
        * (Cylinder(0.75, 1.1) - Cylinder(0.38, 1.6)),
        POLISH,
        "keyless works",
    )
    add(
        "Stem clutch axial groove",
        Pos(12.8, 0, -0.8)
        * Rot(0, 90, 0)
        * (Cylinder(0.80, 0.25) - Cylinder(0.63, 0.6)),
        STEEL,
        "keyless works",
    )
    # Conical face teeth: manufacturing geometry still needs tool / engagement verification.
    wp = Pos(11.2, 0, -0.8) * Rot(0, 90, 0) * (Cylinder(1.05, 0.7) - Cylinder(0.3, 1.2))
    for j in range(12):
        a = j * math.tau / 12
        wp += (
            Pos(10.8, 0.85 * math.sin(a), -0.8 + 0.85 * math.cos(a))
            * Rot(math.degrees(a), 0, 0)
            * Box(0.35, 0.20, 0.50)
        )
    add("Winding pinion twelve face teeth", wp, POLISH, "keyless works")
    gear(
        "Winding crown wheel 40",
        40,
        0.10,
        (10.0, 0),
        -0.8,
        th=0.25,
        bore=0.25,
        group="keyless works",
    )
    # Manual winding downshaft and complete upper-quadrant reduction path.
    add(
        "Manual winding downshaft", cyl(0.22, 2.2, -1.9, 10, 0), POLISH, "keyless works"
    )
    gear(
        "Manual lower crown wheel 40",
        40,
        0.1,
        (10, 0),
        -3.0,
        th=0.10,
        bore=0.22,
        group="keyless works",
    )
    P2 = (7.2, math.sqrt(3.6**2 - 2.8**2))
    mate(
        "Manual lower intermediate 32",
        32,
        0.1,
        P2,
        -3.0,
        "Manual lower crown wheel 40",
        th=0.10,
        bore=0.17,
        group="keyless works",
    )
    P3 = (P2[0] - 4.9, P2[1] + math.sqrt(5.0**2 - 4.9**2))
    mate(
        "Manual reduction wheel 68",
        68,
        0.1,
        P3,
        -3.0,
        "Manual lower intermediate 32",
        th=0.10,
        bore=0.17,
        spokes=4,
        group="keyless works",
    )

    def circle_cross(p, q, r, s):
        dx, dy = q[0] - p[0], q[1] - p[1]
        d = math.hypot(dx, dy)
        a = (r * r - s * s + d * d) / (2 * d)
        h = math.sqrt(r * r - a * a)
        return (p[0] + a * dx / d + h * dy / d, p[1] + a * dy / d - h * dx / d)

    P4 = circle_cross(P3, B, 5.4, 3.8)
    mate(
        "Manual idler wheel 40",
        40,
        0.1,
        P4,
        -3.0,
        "Manual reduction wheel 68",
        th=0.10,
        bore=0.17,
        spokes=4,
        group="keyless works",
    )
    mate(
        "Manual barrel arbor wheel 36",
        36,
        0.1,
        B,
        -3.0,
        "Manual idler wheel 40",
        th=0.10,
        bore=0.55,
        group="keyless works",
    )
    # Explicit setting train placed dial-side, engaged by yoke on stem pull.
    mate(
        "Setting transfer 24",
        24,
        0.10,
        (5.4, 0),
        1.28,
        "Minute intermediate wheel 36",
        th=0.12,
        bore=0.17,
        group="keyless works",
    )
    mate(
        "Setting intermediate 24",
        24,
        0.10,
        (7.8, 0),
        1.28,
        "Setting transfer 24",
        th=0.12,
        bore=0.17,
        group="keyless works",
    )
    mate(
        "Setting wheel 24",
        24,
        0.10,
        (10.2, 0),
        1.28,
        "Setting intermediate 24",
        th=0.12,
        bore=0.17,
        group="keyless works",
    )
    add(
        "Setting lever",
        Pos(11.1, 1.25, 0.25) * Rot(0, 0, -18) * rounded(4.8, 0.6, 0.22, 0.2)
        - cyl(0.23, 0.8, 0.25, 9.0, 1.9),
        POLISH,
        "keyless works",
    )
    add(
        "Clutch yoke fork",
        Pos(12.5, -0.9, -0.8) * Box(0.24, 2.3, 0.75)
        + Pos(12.5, 0, -0.8) * Box(1.6, 0.20, 0.75),
        POLISH,
        "keyless works",
    )
    add(
        "Setting detent leaf spring",
        Pos(10.5, 3.3, 0.15) * Rot(0, 0, 20) * Box(4.8, 0.16, 0.14),
        POLISH,
        "keyless works",
    )
    for j, x in enumerate((11.6, 12.8, 14.0)):
        add(
            "Stem detent notch " + str(j),
            Pos(x, 0, -0.8)
            * Rot(0, 90, 0)
            * (Cylinder(0.32, 0.14) - Cylinder(0.23, 0.4)),
            STEEL,
            "keyless works",
        )
    add(
        "Balance hacking lever",
        Pos(4, -7.5, -1.3) * Box(12, 0.16, 0.16)
        + Pos(10, -3.8, -1.3) * Box(0.16, 7.4, 0.16),
        POLISH,
        "keyless works",
    )
    # Automatic winding: eccentric rotor, ball bearing, two pawled reversers and reduction gears.
    rot = ring(13.65, 10.4, 0.50, -4.10) & Pos(0, -8, -4.10) * Box(30, 16, 2)
    rot += Pos(0, -5.4, -4.10) * rounded(2.6, 10.8, 0.45, 0.6)
    rot += ring(2.1, 0.95, 0.45, -4.10)
    add("Automatic rotor steel arm and rim", rot, STEEL, "automatic")
    add(
        "Rotor high density peripheral weight",
        ring(13.55, 12.10, 0.65, -4.08) & Pos(0, -8, -4.08) * Box(30, 16, 2),
        "#72767d",
        "automatic",
    )
    add("Rotor gear connecting hub", ring(1.08, 0.95, 0.50, -3.75), STEEL, "automatic")
    add("Rotor outer bearing race", ring(0.94, 0.65, 0.44, -3.9), POLISH, "automatic")
    add("Rotor inner bearing race", ring(0.48, 0.30, 0.44, -3.9), POLISH, "automatic")
    for j in range(9):
        a = j * math.tau / 9
        add(
            "Rotor bearing ball %02d" % j,
            Pos(0.56 * math.cos(a), 0.56 * math.sin(a), -3.9) * Sphere(0.085),
            POLISH,
            "automatic",
        )
    gear(
        "Rotor winding gear 36",
        36,
        0.10,
        C,
        -3.45,
        th=0.16,
        bore=0.3,
        group="automatic",
    )
    mate(
        "Auto reverser input 18",
        18,
        0.10,
        (-2.7, 0),
        -3.45,
        "Rotor winding gear 36",
        th=0.16,
        bore=0.2,
        group="automatic",
    )
    gear(
        "Auto reduction 64",
        64,
        0.10,
        (-2.7, 0),
        -3.15,
        th=0.16,
        bore=0.2,
        spokes=5,
        group="automatic",
    )
    A = (-6, math.sqrt(4**2 - 3.3**2))
    mate(
        "Auto intermediate 16",
        16,
        0.10,
        A,
        -3.15,
        "Auto reduction 64",
        th=0.16,
        bore=0.18,
        group="automatic",
    )
    ma = 2 * abs(4 - A[1]) / 48
    gear(
        "Auto output pinion 12", 12, ma, A, -2.8, th=0.14, bore=0.18, group="automatic"
    )
    mate(
        "Barrel winding ratchet 36",
        36,
        ma,
        B,
        -2.8,
        "Auto output pinion 12",
        th=0.14,
        bore=0.55,
        group="automatic",
    )
    for j in range(2):
        xy = (-2.7 + j * 1.3, 0)
        add(
            "Reverser click lever " + str(j),
            Pos(xy[0], xy[1], -3.62)
            * Rot(0, 0, 30 + j * 120)
            * rounded(1.0, 0.18, 0.10, 0.06),
            POLISH,
            "automatic",
        )
        add(
            "Reverser click spring " + str(j),
            Pos(xy[0], xy[1] + 0.45, -3.60) * Box(1.0, 0.07, 0.10),
            POLISH,
            "automatic",
        )
    add(
        "Barrel ratchet click",
        Pos(B[0] - 1.3, B[1] + 0.8, -2.8)
        * Rot(0, 0, -30)
        * rounded(1.5, 0.25, 0.14, 0.08),
        POLISH,
        "automatic",
    )
    add(
        "Ratchet click spring",
        Pos(B[0] - 2, B[1] + 0.4, -2.8) * Box(0.10, 1.8, 0.12),
        POLISH,
        "automatic",
    )
    # Mainplate, real arbor/jewel bores, barrel and balance service apertures.
    plate = cyl(14.5, 0.55, 0.63) - cyl(0.38, 1, 0.63)
    plate -= cyl(6.4, 1.1, 0.63, *B)
    plate -= cyl(3.5, 1.1, 0.63, *BAL)
    for xy in [T, F, E, I, PAL, (2.4, 0), (9, 0), (6.2, math.sqrt(3.6**2 - 2.8**2))]:
        plate -= cyl(0.57, 1, 0.63, *xy)
    for xy in [(-11, -4), (10, 7), (5, -11), (-6, 11)]:
        plate -= cyl(0.27, 1, 0.63, *xy)
    add("Machined mainplate with bearing bores", plate, STEEL, "plates")
    add(
        "Barrel top bridge",
        (Pos(-6, 7, 0.81) * rounded(1.8, 7, 0.24, 0.5) + cyl(1, 0.24, 0.81, *B))
        - cyl(0.89, 1, 0.81, *B),
        STEEL,
        "plates",
    )
    bearing("Barrel arbor upper", *B, 0.81, outer=0.72, bore=0.57, h=0.22)
    # Lower train bridge cut out for the winding path and individual bearing seats.
    bridge = Pos(3, -1, -2.65) * rounded(10, 10, 0.20, 1.7)
    for xy in [C, T, F, E, I, PAL]:
        bridge -= cyl(0.57, 1, -2.5, *xy)
    # clear escape/pallet recess: bridge remains a perimeter frame
    bridge -= Pos(0.7, -3.8, -2.53) * Box(5.5, 3.8, 1)
    add("Going train lower bridge with recessed escapement", bridge, STEEL, "plates")
    add(
        "Balance cock with jewel bore",
        Pos(-6.3, -8.1, -2.65) * rounded(7, 1.65, 0.20, 0.55)
        - cyl(0.67, 1, -2.5, *BAL),
        STEEL,
        "plates",
    )
    add(
        "Upper balance cock with shock setting",
        Pos(-6.3, -8.1, 0.63) * rounded(7, 1.65, 0.20, 0.55) - cyl(0.67, 1, 0.63, *BAL),
        STEEL,
        "plates",
    )
    add(
        "Outer hairspring stud mounting post",
        cyl(0.16, 1.4, -0.05, BAL[0] + 2.62, BAL[1]),
        POLISH,
        "oscillator",
    )
    dx, dy = 7 - PAL[0], -4 - PAL[1]
    L = math.hypot(dx, dy)
    palletcock = (
        Pos((7 + PAL[0]) / 2, (-4 + PAL[1]) / 2, -2.65)
        * Rot(0, 0, math.degrees(math.atan2(dy, dx)) - 90)
        * rounded(1.35, L + 1, 0.2, 0.5)
    )
    palletcock -= cyl(0.49, 1, -2.65, *PAL)
    add("Pallet bearing bridge anchored to train bridge", palletcock, STEEL, "plates")
    # Retaining spacer has crown access and screw bosses; mainplate edge location shoulder.
    spacer = ring(15.8, 14.65, 4.2, -0.6) - Pos(15.3, 0, -0.8) * Box(4, 2, 2)
    add("Movement retaining spacer keyed stem slot", spacer, "#78838e", "plates")
    for j, (x, y) in enumerate([(-11, -4), (10, 7), (5, -11), (-6, 11)]):
        add(
            "Mainplate standoff " + str(j),
            Pos(x, y, 0) * ring(0.60, 0.28, 2.55, -0.95),
            STEEL,
            "plates",
        )
        screw("Movement mounting screw " + str(j), x, y, 1.0, r=0.42, length=3.0)
    for j, xy in enumerate([T, F, E, I, PAL, BAL]):
        x, y = xy
        screw(
            "Bridge retaining screw " + str(j),
            x + 0.9,
            y + 0.7,
            -2.38,
            r=0.30,
            length=0.9,
        )
    # Dial feet and clamped supports located outside the calendar ring.
    for j, a in enumerate((45, 225)):
        x, y = 13.5 * math.cos(math.radians(a)), 13.5 * math.sin(math.radians(a))
        add("Dial foot " + str(j), cyl(0.3, 1.10, 1.25, x, y), POLISH, "dial supports")
        add(
            "Dial foot clamp " + str(j),
            Pos(x, y, 0.8) * Box(1.8, 0.7, 0.18) - cyl(0.32, 0.5, 0.8, x, y),
            STEEL,
            "dial supports",
        )
    # Case and crown: tube, screw-down threads, gasket grooves, crown gasket and guards.
    add(
        "Crown tube with stem bore",
        Pos(20.4, 0, -0.8) * Rot(0, 90, 0) * (Cylinder(0.97, 5.6) - Cylinder(0.47, 6)),
        POLISH,
        "crown",
    )
    add(
        "Crown stem O ring",
        Pos(21.4, 0, -0.8) * Rot(0, 90, 0) * Torus(0.40, 0.12),
        "#232b32",
        "seals",
    )
    crown = (
        Pos(23.4, 0, -0.8)
        * Rot(0, 90, 0)
        * (Cylinder(2.65, 3.0) - Pos(0, 0, -0.5) * Cylinder(1.01, 2.5))
    )
    for j in range(36):
        a = j * math.tau / 36
        crown -= (
            Pos(23.4, 2.68 * math.sin(a), -0.8 + 2.68 * math.cos(a))
            * Rot(0, 90, 0)
            * Cylinder(0.16, 3.5)
        )
    crown -= Pos(22.05, 0, -0.8) * Rot(0, 90, 0) * Cylinder(1.86, 0.5)
    add("Screw down crown hollow knurled cap", crown, POLISH, "crown")
    add(
        "Crown omega relief",
        Pos(24.92, 0, -0.8)
        * Rot(90, 0, 90)
        * extrude(Text("Ω", font_size=2.7, font="Arial"), amount=0.06),
        POLISH,
        "crown",
    )
    add(
        "Crown compression gasket",
        Pos(22.1, 0, -0.8) * Rot(0, 90, 0) * Torus(1.55, 0.25),
        "#25313a",
        "seals",
    )

    # Actual helical ridge, paired female cutter with radial allowance, not cosmetic circles.
    def helical_thread(radius, pitch, length, z):
        path = Helix(pitch, length, radius)
        p = Plane(origin=(radius, 0, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
        f = p * make_face(
            Polygon(
                (-0.10, -pitch * 0.32), (0.10, 0), (-0.10, pitch * 0.32), align=None
            )
        )
        return Pos(0, 0, z) * sweep(f, path=path, is_frenet=True)

    try:
        th = helical_thread(0.99, 0.35, 1.05, -0.525)
        add(
            "Crown tube modeled helical thread",
            Pos(22.6, 0, -0.8) * Rot(0, 90, 0) * th,
            POLISH,
            "crown",
        )
        female = (
            Pos(22.6, 0, -0.8) * Rot(0, 90, 0) * helical_thread(1.03, 0.35, 1.4, -0.525)
        )
        replace(
            "Screw down crown hollow knurled cap",
            lookup("Screw down crown hollow knurled cap") - female,
        )
        thback = helical_thread(17.03, 0.45, 1.35, -3.8)
        add("Caseback modeled helical thread", thback, STEEL, "case")
        # mating case thread relief cut gives 0.04 mm radial allowance
        cutter = helical_thread(17.09, 0.45, 1.575, -3.8)
        insert = ring(17.855, 17.07, 1.55, -2.875) - cutter
        add("Caseback female threaded press insert", insert, STEEL, "case")
    except Exception as e:
        print("THREAD BUILD ERROR", repr(e), flush=True)
        raise
    add(
        "Crown tube shoulder and gasket seat",
        Pos(21.75, 0, -0.8)
        * Rot(0, 90, 0)
        * (Cylinder(1.85, 0.45) - Cylinder(0.47, 0.9)),
        POLISH,
        "crown",
    )
    replace(
        "Crown tube with stem bore",
        lookup("Crown tube with stem bore")
        - Pos(21.4, 0, -0.8) * Rot(0, 90, 0) * Cylinder(0.55, 0.30),
    )
    for y in (-3.7, 3.7):
        add(
            "Integrated crown guard " + str(y),
            Pos(20.1, y, -0.3) * rounded(4.1, 2.1, 4, 0.7),
            STEEL,
            "case",
        )
    # Helium release valve: drilled case path, body, spring, poppet and sealing ring.
    VAL = Pos(-18.0, 10.39, 0) * Rot(0, -90, -30)
    replace(
        "Midcase and drilled lyre lugs",
        lookup("Midcase and drilled lyre lugs") - VAL * Cylinder(1.30, 8),
    )
    add(
        "Helium valve bored body",
        VAL * (Cylinder(1.9, 3.3) - Cylinder(0.9, 4)),
        POLISH,
        "helium valve",
    )
    add(
        "Helium valve screw cap",
        VAL * Pos(0, 0, 2.0) * (Cylinder(2.1, 1.0) - Cylinder(1.1, 1.3)),
        POLISH,
        "helium valve",
    )
    add(
        "Helium valve poppet",
        VAL * (Cylinder(0.7, 2.8) + Pos(0, 0, -1) * Cylinder(1.0, 0.3)),
        POLISH,
        "helium valve",
    )
    add(
        "Helium poppet seal",
        VAL * Pos(0, 0, -1.1) * Torus(0.9, 0.14),
        "#26303a",
        "helium valve",
    )
    vs = Helix(0.30, 1.5, 0.80)
    add(
        "Helium valve compression spring",
        VAL
        * Pos(0, 0, -0.5)
        * sweep(Plane(origin=(0.8, 0, 0), z_dir=vs % 0) * Circle(0.07), path=vs),
        POLISH,
        "helium valve",
    )
    # Unidirectional bezel: 120 real detent teeth and a pair of sprung pawls.
    rat = ring(20.05, 18.45, 0.22, 3.17)
    for j in range(120):
        a = j * math.tau / 120
        tooth = Pos(0, 0, 3.06) * extrude(
            make_face(
                Polygon((18.42, -0.14), (18.08, -0.11), (18.42, 0.16), align=None)
            ),
            amount=0.22,
        )
        rat += Rot(0, 0, math.degrees(a)) * tooth
    add("Bezel 120 tooth detent ring", rat, STEEL, "bezel mechanism")
    for j, a in enumerate((75, 255)):
        add(
            "Bezel sprung click " + str(j),
            Rot(0, 0, a) * Pos(18.15, 0, 3.17) * Box(0.18, 2.1, 0.15),
            POLISH,
            "bezel mechanism",
        )
    add("Bezel friction seal", Pos(0, 0, 3.5) * Torus(18.02, 0.15), "#28303a", "seals")
    # Closed wrist pose: both bracelet runs follow an oval, with interleaved bored knuckles.
    # Equal arc-length stations produce consistent link pitch. Clasp closes the lower gap.
    curve = []
    cum = [0]
    for k in range(2001):
        t = k * math.pi / 2000
        curve.append((23.2 + 12 * math.sin(t), -0.6 - 25 * (1 - math.cos(t))))
        if k:
            cum.append(cum[-1] + math.dist(curve[-1], curve[-2]))
    N = 13
    stations = []
    for k in range(N + 1):
        target = cum[-1] * k / N
        idx = min(range(len(cum)), key=lambda j: abs(cum[j] - target))
        stations.append(curve[idx])
    bracelet_bodies = []
    bracelet_pivots = []
    for sy in (-1, 1):
        for k in range(N):
            y0, z0 = stations[k]
            y1, z1 = stations[k + 1]
            y0 *= sy
            y1 *= sy
            dy, dz = y1 - y0, z1 - z0
            L = math.hypot(dy, dz)
            theta = sy * math.degrees(math.atan2(dz, sy * dy))
            yc, zc = (y0 + y1) / 2, (z0 + z1) / 2
            width = 20 - 2.0 * k / (N - 1)
            body = rounded(width, L - 2.5, 1.65, 0.50)
            for end, xxs in [(1, (-7, 0, 7)), (-1, (-3.5, 3.5))]:
                for x in xxs:
                    kn = Pos(x, end * L / 2, 0) * Rot(0, 90, 0) * Cylinder(1.04, 3.0)
                    web = Pos(x, end * (L / 2 - 0.75), 0) * Box(3.0, 1.5, 1.65)
                    body += kn + web
                body -= (
                    Pos(0, end * L / 2, 0)
                    * Rot(0, 90, 0)
                    * Cylinder(0.65 if k == 0 and end == -1 else 0.45, 24)
                )
            pose = Pos(0, yc, zc) * Rot(theta, 0, 0)
            name = "Bracelet structural link %s %02d" % (sy, k)
            if sy == -1:
                body = body.mirror(Plane.XZ)
            b = add(name, pose * body, STEEL, "bracelet structure")
            bracelet_bodies.append((sy, k, b))
            widths = [
                width * 0.225,
                width * 0.14,
                width * 0.27,
                width * 0.14,
                width * 0.225,
            ]
            x = -width / 2
            for j, w in enumerate(widths):
                cap = rounded(w - 0.10, L - 0.45, 0.40, 0.22)
                cap = fillet(cap.edges().filter_by(Axis.Z, reverse=True), 0.10)
                plate = Pos(x + w / 2, 0, 0.98) * cap
                add(
                    "Bracelet brushed polished cap %s %02d %d" % (sy, k, j),
                    pose * plate,
                    POLISH if j in (1, 3) else "#929ba5",
                    "bracelet finish",
                )
                x += w
            # pin at lower station, shank clearance and slotted removable end screw
            pin = Pos(0, y1, z1) * Rot(0, 90, 0) * Cylinder(0.40, width - 0.4)
            add("Bracelet pin %s %02d" % (sy, k), pin, POLISH, "bracelet pins")
            cap = Pos(width / 2 - 0.2, y1, z1) * Rot(0, 90, 0) * Cylinder(0.65, 0.35)
            cap -= Pos(width / 2, y1, z1) * Box(0.35, 0.14, 0.8)
            add(
                "Bracelet retaining screw %s %02d" % (sy, k),
                cap,
                POLISH,
                "bracelet pins",
            )
            bracelet_pivots.append(
                dict(
                    side=sy,
                    index=k,
                    point=[0, y1, z1],
                    axis=[1, 0, 0],
                    pin_diameter=0.8,
                    bore_diameter=0.9,
                )
            )
        # telescopic spring bar: hollow tube, spring and retractable tips
        add(
            "Lug spring bar tube " + str(sy),
            Pos(0, sy * 23.2, -0.6)
            * Rot(0, 90, 0)
            * (Cylinder(0.60, 18.6) - Cylinder(0.38, 19.0)),
            POLISH,
            "bracelet pins",
        )
        for sx in (-1, 1):
            add(
                "Lug spring bar telescopic tip %s %s" % (sy, sx),
                Pos(sx * 10.3, sy * 23.2, -0.6) * Rot(0, 90, 0) * Cylinder(0.34, 3.1),
                POLISH,
                "bracelet pins",
            )
        # tube spring is continuous helix along X
        h = Helix(0.45, 6, 0.28)
        add(
            "Spring bar internal coil " + str(sy),
            Pos(-3, sy * 23.2, -0.6)
            * Rot(0, 90, 0)
            * sweep(Plane(origin=(0.28, 0, 0), z_dir=h % 0) * Circle(0.055), path=h),
            POLISH,
            "bracelet pins",
        )
    # Two folding clasp blades, pin bores, pushbutton catches and fine adjustment rack.
    zcl = -50.6
    shell = Pos(0, 0, zcl - 1.25) * rounded(19.2, 48.4, 2.6, 2.0) - Pos(
        0, 0, zcl - 0.4
    ) * rounded(16.8, 45.8, 2.9, 1.4)
    for y in (-23.2, 23.2):
        shell -= Pos(0, y, zcl) * Box(17.2, 3.5, 3.0)
        for x in (-9.2, 9.2):
            shell += (
                Pos(x, y, zcl)
                * Rot(0, 90, 0)
                * (Cylinder(0.95, 1.0) - Cylinder(0.50, 1.5))
            )
    add("Deployant clasp machined hollow shell", shell, STEEL, "clasp")
    for sy in (-1, 1):
        blade = Pos(0, sy * 11.6, zcl) * rounded(14.2, 21.0, 0.8, 1.0)
        for joint, xs in [
            (0, (-5, 0, 5) if sy == -1 else (-2.5, 2.5)),
            (sy * 23.2, (-3.5, 3.5)),
        ]:
            for x in xs:
                blade += (
                    Pos(x, joint, zcl)
                    * Rot(0, 90, 0)
                    * Cylinder(0.9, 2.2 if joint == 0 else 3.0)
                )
                direction = sy if joint == 0 else -sy
                blade += Pos(x, joint + direction * 0.9, zcl) * Box(
                    2.2 if joint == 0 else 3.0, 0.7, 0.8
                )
        for y in (0, sy * 23.2):
            blade -= Pos(0, y, zcl) * Rot(0, 90, 0) * Cylinder(0.5, 22)
        add("Deployant folding blade " + str(sy), blade, POLISH, "clasp")
        for y in (0, sy * 23.2):
            if y == 0 and sy == 1:
                continue
            if y != 0:
                remove("Bracelet pin %s %02d" % (sy, N - 1))
            add(
                "Deployant hinge pin " + str(sy) + str(y),
                Pos(0, y, zcl) * Rot(0, 90, 0) * Cylinder(0.42, 19.2),
                POLISH,
                "clasp",
            )
        add(
            "Clasp pushbutton " + str(sy),
            Pos(sy * 9, 1, zcl) * rounded(2.3, 5.3, 1.3, 0.5),
            POLISH,
            "clasp",
        )
        add(
            "Clasp catch hook " + str(sy),
            Pos(sy * 6.7, 1, zcl + 0.2) * Box(1, 3, 0.8),
            STEEL,
            "clasp",
        )
        h = Helix(0.35, 2, 0.35)
        add(
            "Clasp pushbutton return spring " + str(sy),
            Pos(sy * 5, 1, zcl)
            * Rot(0, 90, 0)
            * sweep(Plane(origin=(0.35, 0, 0), z_dir=h % 0) * Circle(0.06), path=h),
            POLISH,
            "clasp",
        )
    rack = Pos(0, 14, zcl + 0.7) * Box(11, 12, 0.6)
    for k in range(6):
        rack -= Pos(0, 9 + k * 1.8, zcl + 1) * Box(10, 0.35, 0.5)
    add("Clasp six position adjustment rack", rack, STEEL, "clasp")
    add(
        "Clasp omega logo",
        Pos(0, 0, zcl - 2.58)
        * Rot(180, 0, 0)
        * extrude(Text("Ω", font_size=5.5, font="Arial"), amount=0.035),
        POLISH,
        "clasp",
    )
    print("Full geometry created", len(parts), flush=True)
    # Audit every modeled part; analytical mesh checks use the same sampled involute profile.
    checks = {
        "status": "draft",
        "parts": len(parts),
        "invalid_parts": [],
        "meshes": meshes,
        "fits": fitted,
        "limits": [
            "Custom Swiss-lever mechanism, not Omega Co-Axial 8800 production geometry.",
            "Escapement lock/drop, winding face gear contact, clutch engagement and calendar jump timing are NOT dynamically proven.",
            "No rate, torque, power reserve, fatigue, shock or 300m pressure certification.",
            "Micro threads are geometric nominal interfaces; production tolerances and thread gauges remain necessary.",
        ],
    }
    for p in parts:
        if not p.is_valid:
            checks["invalid_parts"].append(p.label)
    checks["oscillator_sizing"] = {
        "target_hz": 3,
        "cad_inertia_kg_m2": balance_inertia,
        "assumed_E_N_mm2": hair_E,
        "ribbon_width_mm": 0.1,
        "ribbon_thickness_mm": hair_thickness,
        "spring_length_mm": hair_length,
        "torsional_stiffness_Nmm_per_rad": hair_k,
        "method": "First-order EI/L ribbon stiffness and CAD inertia; ignores amplitude dependence, escapement impulses, thermal drift and material variation.",
    }
    checks["train_ratios"] = {
        "center_to_fourth": 80 / 10 * 75 / 10,
        "fourth_to_escape": 96 / 8,
        "center_to_escape": 80 / 10 * 75 / 10 * 96 / 8,
        "hour_reduction": 36 / 12 * 40 / 10,
        "calendar_drive_hours": 12 * 80 / 40,
        "escape_teeth": 15,
        "design_beats_per_second": 15 * 2 / (3600 / (80 / 10 * 75 / 10 * 96 / 8)),
    }
    checks["mesh_sweeps"] = []
    for pair in meshes:
        a, b = spec(pair["driver"]), spec(pair["driven"])
        areas = []
        for k in range(25):
            theta = k / 24 * 360 / a["teeth"]
            pa = stranslate(
                srotate(gear2d[a["name"]], a["phase_deg"] + theta, origin=(0, 0)),
                *a["center"],
            )
            pb = stranslate(
                srotate(
                    gear2d[b["name"]],
                    b["phase_deg"] - theta * a["teeth"] / b["teeth"],
                    origin=(0, 0),
                ),
                *b["center"],
            )
            areas.append(pa.intersection(pb).area)
        rp1 = a["module_mm"] * a["teeth"] / 2
        rp2 = b["module_mm"] * b["teeth"] / 2
        m = a["module_mm"]
        pa = math.pi / 6
        contact = (
            math.sqrt((rp1 + m) ** 2 - (rp1 * math.cos(pa)) ** 2)
            + math.sqrt((rp2 + m) ** 2 - (rp2 * math.cos(pa)) ** 2)
            - (rp1 + rp2) * math.sin(pa)
        ) / (math.pi * m * math.cos(pa))
        checks["mesh_sweeps"].append(
            dict(
                driver=a["name"],
                driven=b["name"],
                samples=25,
                max_polygon_interference_area_mm2=max(areas),
                transverse_contact_ratio=contact,
            )
        )
    # Selected exact OCCT intersections: all gear pairs and adjacent bracelet bodies.
    checks["exact_intersections"] = []

    def intersection_check(a, b, category):
        v = a.intersect(b)
        vol = sum(x.volume for x in v.solids()) if v else 0
        checks["exact_intersections"].append(
            {"a": a.label, "b": b.label, "category": category, "intersection_mm3": vol}
        )

    for pair in meshes:
        intersection_check(
            lookup(pair["driver"]), lookup(pair["driven"]), "gear mesh nominal pose"
        )
    for sy in (-1, 1):
        bs = [p for s, k, p in bracelet_bodies if s == sy]
        for a, b in zip(bs, bs[1:]):
            intersection_check(a, b, "adjacent bracelet link in wrist pose")
    for a, b in [
        ("Hour skeleton hand", "Minute skeleton hand"),
        ("Minute skeleton hand", "Red seconds hand"),
        ("Red seconds hand", "Sapphire crystal"),
        ("Seconds staff solid 0.24", "Minute cannon tube ID0.30 OD0.56"),
        ("Minute cannon tube ID0.30 OD0.56", "Hour pipe ID0.64 OD1.00"),
    ]:
        intersection_check(lookup(a), lookup(b), "hands at displayed pose")
    for g in gear_specs:
        for name in [
            "Machined mainplate with bearing bores",
            "Going train lower bridge with recessed escapement",
            "Balance cock with jewel bore",
            "Barrel top bridge",
            "Movement retaining spacer keyed stem slot",
        ]:
            shape = lookup(g["name"])
            fixed = lookup(name)
            a, b = shape.bounding_box(), fixed.bounding_box()
            if all(
                getattr(a.max, k) > getattr(b.min, k)
                and getattr(b.max, k) > getattr(a.min, k)
                for k in "XYZ"
            ):
                intersection_check(shape, fixed, "moving gear vs stationary support")
    for part in parts:
        if any(
            w in part.label.lower()
            for w in [
                "stepped arbor",
                "manual winding downshaft",
                "solid 0.24",
                "cannon tube",
                "hour pipe",
            ]
        ):
            box = part.bounding_box()
            center = box.center()
            for g in gear_specs:
                if (
                    math.hypot(center.X - g["center"][0], center.Y - g["center"][1])
                    < 0.02
                ):
                    continue
                other = lookup(g["name"])
                b = other.bounding_box()
                if all(
                    getattr(box.max, k) > getattr(b.min, k)
                    and getattr(b.max, k) > getattr(box.min, k)
                    for k in "XYZ"
                ):
                    intersection_check(
                        part, other, "nonmating shaft versus rotating gear"
                    )
    known_pairs = {frozenset((m["driver"], m["driven"])) for m in meshes}
    for i, g in enumerate(gear_specs):
        for h in gear_specs[i + 1 :]:
            if frozenset((g["name"], h["name"])) in known_pairs:
                continue
            if abs(g["z"] - h["z"]) >= (g["width"] + h["width"]) / 2 - 1e-7:
                continue
            if math.dist(g["center"], h["center"]) < 0.01:
                continue
            if (
                math.dist(g["center"], h["center"])
                > (g["teeth"] + 2) * g["module_mm"] / 2
                + (h["teeth"] + 2) * h["module_mm"] / 2
            ):
                continue
            intersection_check(
                lookup(g["name"]), lookup(h["name"]), "nonmeshing gear pair"
            )
    checks["bracelet_articulation_sweep"] = []
    for sy in (-1, 1):
        bs = [p for side, k, p in bracelet_bodies if side == sy]
        for k, (a, b) in enumerate(zip(bs, bs[1:])):
            y, z = stations[k + 1]
            axis = Axis((0, sy * y, z), (1, 0, 0))
            vs = []
            for angle in (-4, 0, 4):
                inter = a.intersect(b.rotate(axis, angle))
                vs.append(sum(v.volume for v in inter.solids()) if inter else 0)
            checks["bracelet_articulation_sweep"].append(
                {
                    "side": sy,
                    "joint": k,
                    "angle_offsets_deg": [-4, 0, 4],
                    "max_intersection_mm3": max(vs),
                }
            )
    for sy in (-1, 1):
        intersection_check(
            lookup("Deployant folding blade " + str(sy)),
            lookup("Deployant clasp machined hollow shell"),
            "folded clasp blade versus shell",
        )
        last = next(b for side, k, b in bracelet_bodies if side == sy and k == N - 1)
        intersection_check(
            last,
            lookup("Deployant clasp machined hollow shell"),
            "last bracelet link versus clasp shell",
        )
        intersection_check(
            last,
            lookup("Deployant folding blade " + str(sy)),
            "last bracelet link versus folding blade",
        )
    intersection_check(
        lookup("Deployant folding blade -1"),
        lookup("Deployant folding blade 1"),
        "clasp central hinge",
    )
    intersection_check(
        lookup("Caseback modeled helical thread"),
        lookup("Caseback female threaded press insert"),
        "caseback male and female thread",
    )
    intersection_check(
        lookup("Caseback threaded skirt"),
        lookup("Caseback female threaded press insert"),
        "caseback neck versus threaded receiver",
    )
    intersection_check(
        lookup("Caseback threaded skirt"),
        lookup("Midcase and drilled lyre lugs"),
        "caseback neck versus case",
    )
    intersection_check(
        lookup("Crown tube modeled helical thread"),
        lookup("Screw down crown hollow knurled cap"),
        "crown male and female thread",
    )
    checks["nominal_hand_layer_clearances_mm"] = {
        "dial_hour": 0.26,
        "hour_minute": 0.295,
        "minute_seconds": 0.315,
        "seconds_crystal": 0.5575,
        "seconds_minute_radial": 0.03,
        "minute_hour_radial": 0.04,
    }
    checks["bracelet"] = {
        "pose": "closed oval wrist loop",
        "link_count": N * 2,
        "joint_count": len(bracelet_pivots),
        "pin_bore_diametral_clearance_mm": 0.1,
        "case_diameter_mm": 42.0,
        "case_thickness_mm": 13.56,
        "lug_to_lug_mm": 49.90,
        "stations_yz_mm": stations,
        "pivots": bracelet_pivots,
    }
    (OUT / "gears.json").write_text(json.dumps(gear_specs, indent=2))
    (OUT / "groups.json").write_text(json.dumps(groups, indent=2))
    (OUT / "part_names.json").write_text(json.dumps([p.label for p in parts]))
    checks["case_geometry_measured_mm"] = {
        "bezel_x_extent": lookup("Scalloped rotating bezel").bounding_box().size.X,
        "lug_to_lug": lookup("Midcase and drilled lyre lugs").bounding_box().size.Y,
        "top_crystal_z": lookup("Sapphire crystal").bounding_box().max.Z,
        "bottom_caseback_z": lookup("Exhibition caseback steel rim")
        .bounding_box()
        .min.Z,
    }

    write_json(OUT / "inspection.json", checks)
    print("Validation saved", flush=True)
    assembly = Compound(children=parts, label="Detailed Seamaster style wristwatch")
    export_step(assembly, OUT / "watch.step")
    export_brep(assembly, OUT / "watch.brep")
    print("STEP and BREP exported", flush=True)
    ctx = SimpleNamespace(
        shape=assembly,
        units="mm",
        features=[],
        project="seamaster",
        rev=1,
        status="draft",
        scene_options={"deviation": 0.12, "angular_tolerance": 0.30},
    )
    manifest = make_colored_scene(ctx, OUT / "scene", colors)
    print(
        "SCENE",
        manifest.triangles,
        "triangles",
        manifest.total_bytes,
        "bytes",
        flush=True,
    )
    views = prepare_views(groups, manifest.parts)
    (OUT / "views.json").write_text(json.dumps(views))
    (OUT / "presentation.json").write_text(json.dumps({"style": "shaded"}))
    failures = validation_failures(checks)
    checks["selected_checks_passed"] = not failures
    checks["selected_check_failures"] = failures
    write_json(OUT / "inspection.json", checks)
    with (OUT / "parts.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["part_number", "component_name", "subsystem"])
        for index, obj in enumerate(parts, start=1):
            writer.writerow(
                [
                    index,
                    obj.label,
                    next((g for g, names in groups.items() if obj.label in names), ""),
                ]
            )
    # Hide only the glass for the default inspection; every original part stays saved.
    presentation = {
        "hide": ["Sapphire crystal", "Caseback sapphire"],
        "style": "shaded",
    }
    for recipe in views:
        if recipe["id"] == "all":
            recipe["presentation"] = presentation
    write_json(OUT / "views.json", views)
    write_json(OUT / "presentation.json", presentation)
    export_bundle(
        OUT / "scene",
        OUT / "viewer",
        title="Seamaster-inspired wristwatch · draft",
        view="iso",
        presentation=presentation,
        views=views,
        controls="cad",
    )
    exported = import_brep(OUT / "watch.brep")
    export_check = {
        "exported_brep_valid": exported.is_valid,
        "solid_count": len(exported.solids()),
        "volume_mm3": exported.volume,
    }
    write_json(OUT / "export_check.json", export_check)
    if not export_check["exported_brep_valid"]:
        failures.append("Exported BREP is invalid")
        checks["selected_checks_passed"] = False
        write_json(OUT / "inspection.json", checks)
    print("Outputs:", OUT, flush=True)
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".aad/examples/seamaster"),
        help="Generated files (default: .aad/examples/seamaster)",
    )
    parser.add_argument(
        "--allow-failed-checks",
        action="store_true",
        help="Keep a zero exit status for an explicitly unverified inspection build; failures remain in inspection.json",
    )
    args = parser.parse_args(argv)
    checks = build_watch(args.output_dir)
    failures = checks["selected_check_failures"]
    for failure in failures:
        print("FAILED:", failure)
    print(
        "Status: draft; selected checks "
        + ("FAILED" if failures else "passed")
        + ". No operational certification."
    )
    return 1 if failures and not args.allow_failed_checks else 0


if __name__ == "__main__":
    raise SystemExit(main())
