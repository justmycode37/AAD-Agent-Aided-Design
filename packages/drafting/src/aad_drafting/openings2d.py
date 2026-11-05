"""Recognize measured circular, rectangular and obround interior cutouts."""

import math


def _rectangle(edges, tolerance):
    if len(edges) != 4 or any(edge["geometry"]["kind"] != "line" for edge in edges):
        return None
    vertices = []
    for edge in edges:
        for point in (edge["geometry"]["start"], edge["geometry"]["end"]):
            if not any(math.dist(point[:2], old) <= tolerance for old in vertices):
                vertices.append(tuple(point[:2]))
    if len(vertices) != 4:
        return None
    center = tuple(sum(point[axis] for point in vertices) / 4 for axis in (0, 1))
    vertices.sort(key=lambda point: math.atan2(point[1] - center[1], point[0] - center[0]))
    vectors = [(vertices[(i + 1) % 4][0] - vertices[i][0],
                vertices[(i + 1) % 4][1] - vertices[i][1]) for i in range(4)]
    lengths = [math.hypot(*vector) for vector in vectors]
    if min(lengths) <= tolerance:
        return None
    if any(abs(vectors[i][0] * vectors[(i+1)%4][0] + vectors[i][1] * vectors[(i+1)%4][1])
           > tolerance * max(lengths[i], lengths[(i+1)%4]) for i in range(4)):
        return None
    if any(abs(lengths[i] - lengths[i+2]) > tolerance for i in range(2)):
        return None
    return {"kind": "rectangle", "width": min(lengths), "length": max(lengths), "center": list(center)}


def _obround(edges, tolerance):
    lines = [edge for edge in edges if edge["geometry"]["kind"] == "line"]
    arcs = [edge for edge in edges if edge["geometry"]["kind"] == "arc"]
    if len(edges) != 4 or len(lines) != 2 or len(arcs) != 2:
        return None
    first, second = [edge["geometry"] for edge in arcs]
    c0, c1 = first["center"], second["center"]
    radius = first["radius"]
    length = math.dist(c0[:2], c1[:2])
    if length <= tolerance or abs(radius - second["radius"]) > tolerance:
        return None
    axis = ((c1[0] - c0[0]) / length, (c1[1] - c0[1]) / length)
    normal = (-axis[1], axis[0])
    expected = [(center[0] + sign * radius * normal[0], center[1] + sign * radius * normal[1])
                for center in (c0, c1) for sign in (-1, 1)]
    for line in lines:
        if abs(line["length"] - length) > tolerance:
            return None
        for endpoint in (line["geometry"]["start"], line["geometry"]["end"]):
            if not any(math.dist(endpoint[:2], corner) <= tolerance for corner in expected):
                return None
    for index, arc in enumerate((first, second)):
        direction = -1 if arc["clockwise"] else 1
        sweep = ((arc["end_angle"] - arc["start_angle"]) * direction) % 360
        if abs(math.radians(sweep) * radius - math.pi * radius) > tolerance:
            return None
        mid = math.radians(arc["start_angle"] + direction * sweep / 2)
        outward = math.cos(mid) * axis[0] + math.sin(mid) * axis[1]
        if outward * (-1 if index == 0 else 1) < 1 - tolerance / radius:
            return None
    return {"kind": "obround", "width": 2 * radius, "length": length + 2 * radius,
            "center": [(c0[0] + c1[0]) / 2, (c0[1] + c1[1]) / 2]}


def measured_openings(evidence):
    """Return recognized widths and unknown contour IDs, using proven edges."""
    # Feature recognition uses local edge lengths, not absolute coordinates or
    # the (possibly much larger) endpoint-joining tolerance. A translated
    # trapezoid must not become a rectangle through a looser classification.
    magnitude = max((edge["length"] for contour in evidence["closed_contours"]
                     for edge in contour["edges"]), default=1.0)
    tolerance = max(1e-9, magnitude * math.ulp(1.0) * 64)
    circles = {hole["contour"]: hole for hole in evidence["holes"]}
    openings, unknown = [], []
    for contour in evidence["closed_contours"]:
        depth = contour["nesting_depth"]
        if not contour["valid"] or depth is None or depth % 2 == 0:
            continue
        circle = circles.get(contour["id"])
        if circle:
            measured = {"kind": "circle", "width": circle["diameter"], "length": circle["diameter"],
                        "center": circle["center"][:2]}
        else:
            measured = _rectangle(contour["edges"], tolerance) or _obround(contour["edges"], tolerance)
        if measured is None:
            unknown.append(contour["id"])
        else:
            openings.append({**measured, "contour": contour["id"], "source_entities": contour["source_entities"]})
    return openings, unknown


def min_slot_check(evidence, minimum):
    openings, unknown = measured_openings(evidence)
    below = [opening for opening in openings if opening["width"] < minimum]
    complete = evidence["status"]["complete"] and not unknown
    status = "fail" if below else "pass" if complete and openings else "skip"
    message = (f"{len(below)} measured interior opening(s) are narrower than min_slot." if below else
               f"Measured {len(openings)} circular, rectangular or obround interior opening(s)." if status == "pass" else
               "Minimum-slot coverage is incomplete or no supported interior cutouts exist.")
    return status, message, {"openings": openings, "unsupported_contours": unknown}
