"""Vector and raster writers for a shared drawing-sheet primitive list."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable
from xml.etree.ElementTree import Element, SubElement, tostring


SVG_NS = "http://www.w3.org/2000/svg"
MM_TO_PT = 72.0 / 25.4


@dataclass(frozen=True)
class Primitive:
    kind: str
    layer: str
    points: tuple[tuple[float, float], ...] = ()
    center: tuple[float, float] | None = None
    radius: float | None = None
    start_angle: float | None = None
    end_angle: float | None = None
    ccw: bool = True
    text: str | None = None
    height: float = 3.0
    align: str = "left"
    rotation: float = 0.0
    width: float = 0.18
    dash: bool = False
    dimension_id: str | None = None
    view_name: str | None = None


def line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    layer: str = "VISIBLE",
    width: float = 0.18,
    dash: bool = False,
) -> Primitive:
    return Primitive(
        "line",
        layer,
        points=((float(x1), float(y1)), (float(x2), float(y2))),
        width=width,
        dash=dash,
    )


def polyline(
    points: Iterable[tuple[float, float]],
    layer: str = "VISIBLE",
    width: float = 0.18,
    dash: bool = False,
) -> Primitive:
    return Primitive(
        "polyline",
        layer,
        points=tuple((float(x), float(y)) for x, y in points),
        width=width,
        dash=dash,
    )


def circle(
    center: tuple[float, float],
    radius: float,
    layer: str = "VISIBLE",
    width: float = 0.18,
    dash: bool = False,
) -> Primitive:
    return Primitive(
        "circle",
        layer,
        center=(float(center[0]), float(center[1])),
        radius=float(radius),
        width=width,
        dash=dash,
    )


def arc(
    center: tuple[float, float],
    radius: float,
    start_angle: float,
    end_angle: float,
    ccw: bool,
    layer: str = "VISIBLE",
    width: float = 0.18,
    dash: bool = False,
) -> Primitive:
    return Primitive(
        "arc",
        layer,
        center=(float(center[0]), float(center[1])),
        radius=float(radius),
        start_angle=float(start_angle),
        end_angle=float(end_angle),
        ccw=bool(ccw),
        width=width,
        dash=dash,
    )


def text(
    value: str,
    x: float,
    y: float,
    height: float = 3.0,
    layer: str = "ANNOTATION",
    align: str = "left",
    rotation: float = 0.0,
    dimension_id: str | None = None,
) -> Primitive:
    return Primitive(
        "text",
        layer,
        points=((float(x), float(y)),),
        text=str(value),
        height=float(height),
        align=align,
        rotation=float(rotation),
        dimension_id=dimension_id,
    )


def _fmt(value: float) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"


def write_svg(path: Path, width_mm: float, height_mm: float, entities: list[Primitive]) -> None:
    from xml.etree.ElementTree import register_namespace

    register_namespace("", SVG_NS)
    root = Element(
        f"{{{SVG_NS}}}svg",
        {
            "width": f"{_fmt(width_mm)}mm",
            "height": f"{_fmt(height_mm)}mm",
            "viewBox": f"0 0 {_fmt(width_mm)} {_fmt(height_mm)}",
            "version": "1.1",
        },
    )
    geometry = SubElement(
        root,
        f"{{{SVG_NS}}}g",
        {
            "transform": f"translate(0 {_fmt(height_mm)}) scale(1 -1)",
            "fill": "none",
            "stroke": "#111",
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
        },
    )

    for entity in entities:
        if entity.kind == "text":
            continue
        attributes = {
            "data-layer": entity.layer,
            "stroke-width": _fmt(entity.width),
            "stroke-dasharray": "1.4 0.9" if entity.dash else "none",
        }
        if entity.kind == "line":
            (x1, y1), (x2, y2) = entity.points
            SubElement(
                geometry,
                f"{{{SVG_NS}}}line",
                {
                    **attributes,
                    "x1": _fmt(x1),
                    "y1": _fmt(y1),
                    "x2": _fmt(x2),
                    "y2": _fmt(y2),
                },
            )
        elif entity.kind == "polyline":
            points = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in entity.points)
            SubElement(
                geometry,
                f"{{{SVG_NS}}}polyline",
                {**attributes, "points": points},
            )
        elif entity.kind == "circle":
            SubElement(
                geometry,
                f"{{{SVG_NS}}}circle",
                {
                    **attributes,
                    "cx": _fmt(entity.center[0]),
                    "cy": _fmt(entity.center[1]),
                    "r": _fmt(entity.radius),
                },
            )
        elif entity.kind == "arc":
            start, end = _arc_endpoints(entity)
            sweep, large = _arc_sweep(entity)
            path_data = (
                f"M {_fmt(start[0])} {_fmt(start[1])} "
                f"A {_fmt(entity.radius)} {_fmt(entity.radius)} 0 {large} {sweep} "
                f"{_fmt(end[0])} {_fmt(end[1])}"
            )
            SubElement(
                geometry,
                f"{{{SVG_NS}}}path",
                {**attributes, "d": path_data},
            )

    labels = SubElement(
        root,
        f"{{{SVG_NS}}}g",
        {"fill": "#111", "stroke": "none", "font-family": "Arial, Helvetica, sans-serif"},
    )
    for entity in entities:
        if entity.kind != "text":
            continue
        x, y = entity.points[0]
        anchor = {"left": "start", "center": "middle", "right": "end"}.get(entity.align, "start")
        label = SubElement(
            labels,
            f"{{{SVG_NS}}}text",
            {
                "x": _fmt(x),
                "y": _fmt(height_mm - y),
                "font-size": _fmt(entity.height),
                "text-anchor": anchor,
                "data-layer": entity.layer,
            },
        )
        if entity.dimension_id is not None:
            label.set("data-dimension-id", entity.dimension_id)
        label.text = entity.text or ""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tostring(root, encoding="utf-8", xml_declaration=True))


def _arc_sweep(entity: Primitive) -> tuple[int, int]:
    start, end = entity.start_angle, entity.end_angle
    if entity.ccw:
        degrees = (end - start) % 360
        sweep = 1
    else:
        degrees = (start - end) % 360
        sweep = 0
    return sweep, int(degrees > 180)


def _arc_endpoints(entity: Primitive) -> tuple[tuple[float, float], tuple[float, float]]:
    cx, cy = entity.center
    radius = entity.radius
    start = math.radians(entity.start_angle)
    end = math.radians(entity.end_angle)
    return (
        (cx + radius * math.cos(start), cy + radius * math.sin(start)),
        (cx + radius * math.cos(end), cy + radius * math.sin(end)),
    )


def write_dxf(path: Path, width_mm: float, height_mm: float, entities: list[Primitive]) -> None:
    import ezdxf

    document = ezdxf.new("R2013")
    document.header["$INSUNITS"] = 4
    document.header["$MEASUREMENT"] = 1
    document.header["$LUNITS"] = 2
    document.header["$LUPREC"] = 2
    # Some CAD readers recognize conventional names instead of custom patterns.
    if "HIDDEN" not in document.linetypes:
        document.linetypes.new(
            "HIDDEN",
            dxfattribs={"description": "AIAD hidden edges", "pattern": [2.3, 1.4, -0.9]},
        )

    layer_colors = {
        "VISIBLE": 7,
        "HIDDEN": 8,
        "DIMENSIONS": 7,
        "ANNOTATION": 7,
        "BORDER": 7,
        "TITLE": 7,
    }
    for entity in entities:
        layer_colors.setdefault(entity.layer, 7)
    for layer_name, color in layer_colors.items():
        if layer_name not in document.layers:
            document.layers.new(
                layer_name,
                dxfattribs={"color": color, "lineweight": 18},
            )

    modelspace = document.modelspace()
    for entity in entities:
        layer = entity.layer if entity.layer in layer_colors else "ANNOTATION"
        attributes = {
            "layer": layer,
            "lineweight": max(9, min(50, int(entity.width * 100))),
        }
        if entity.dash:
            attributes["linetype"] = "HIDDEN"

        if entity.kind == "line":
            modelspace.add_line(entity.points[0], entity.points[1], dxfattribs=attributes)
        elif entity.kind == "polyline":
            modelspace.add_lwpolyline(entity.points, dxfattribs=attributes)
        elif entity.kind == "circle":
            modelspace.add_circle(
                entity.center,
                entity.radius,
                dxfattribs=attributes,
            )
        elif entity.kind == "arc":
            start = entity.start_angle
            end = entity.end_angle
            if not entity.ccw:
                start, end = end, start
            modelspace.add_arc(
                entity.center,
                entity.radius,
                start_angle=start,
                end_angle=end,
                dxfattribs=attributes,
            )
        elif entity.kind == "text":
            from ezdxf.enums import TextEntityAlignment

            alignments = {
                "left": TextEntityAlignment.LEFT,
                "center": TextEntityAlignment.CENTER,
                "right": TextEntityAlignment.RIGHT,
            }
            dxf_text = modelspace.add_text(
                entity.text or "",
                dxfattribs={
                    **attributes,
                    "height": entity.height,
                    "rotation": entity.rotation,
                },
            )
            dxf_text.set_placement(
                entity.points[0],
                align=alignments.get(entity.align, TextEntityAlignment.LEFT),
            )

    auditor=document.audit()
    if auditor.errors:
        raise ValueError(f"DXF audit failed with {len(auditor.errors)} errors.")
    path.parent.mkdir(parents=True, exist_ok=True)
    document.saveas(path)


def write_pdf(path: Path, width_mm: float, height_mm: float, entities: list[Primitive]) -> None:
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(
        str(path),
        pagesize=(width_mm * MM_TO_PT, height_mm * MM_TO_PT),
        pageCompression=1,
        invariant=1,
    )
    pdf.setTitle("AIAD drawing sheet")
    pdf.setAuthor("AIAD")
    pdf.scale(MM_TO_PT, MM_TO_PT)

    for entity in entities:
        pdf.saveState()
        pdf.setLineWidth(entity.width)
        pdf.setLineCap(0)
        pdf.setLineJoin(0)
        pdf.setDash([1.4, 0.9] if entity.dash else [])
        if entity.kind == "line":
            (x1, y1), (x2, y2) = entity.points
            pdf.line(x1, y1, x2, y2)
        elif entity.kind == "polyline":
            if len(entity.points) >= 2:
                path_object = pdf.beginPath()
                path_object.moveTo(*entity.points[0])
                for point in entity.points[1:]:
                    path_object.lineTo(*point)
                pdf.drawPath(path_object, stroke=1, fill=0)
        elif entity.kind == "circle":
            pdf.circle(*entity.center, entity.radius, stroke=1, fill=0)
        elif entity.kind == "arc":
            start, extent = _reportlab_arc_angles(entity)
            cx, cy = entity.center
            radius = entity.radius
            pdf.arc(
                cx - radius,
                cy - radius,
                cx + radius,
                cy + radius,
                startAng=start,
                extent=extent,
            )
        elif entity.kind == "text":
            x, y = entity.points[0]
            pdf.translate(x, y)
            pdf.rotate(entity.rotation)
            pdf.setFont("Helvetica", entity.height)
            if entity.align == "center":
                pdf.drawCentredString(0, 0, entity.text or "")
            elif entity.align == "right":
                pdf.drawRightString(0, 0, entity.text or "")
            else:
                pdf.drawString(0, 0, entity.text or "")
        pdf.restoreState()

    pdf.showPage()
    pdf.save()


def _reportlab_arc_angles(entity: Primitive) -> tuple[float, float]:
    start, end = entity.start_angle, entity.end_angle
    if entity.ccw:
        return start, (end - start) % 360
    return start, -((start - end) % 360)


def write_png(path: Path, width_mm: float, height_mm: float, entities: list[Primitive]) -> None:
    from PIL import Image, ImageDraw

    raster_scale = min(4.0, 2400.0 / max(width_mm, height_mm))
    raster_scale = max(raster_scale, 1.0)
    width_px = max(1, round(width_mm * raster_scale))
    height_px = max(1, round(height_mm * raster_scale))
    image = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(image)

    def screen(point: tuple[float, float]) -> tuple[int, int]:
        return (
            round(point[0] * raster_scale),
            height_px - round(point[1] * raster_scale),
        )

    def draw_segment(
        start: tuple[float, float],
        end: tuple[float, float],
        fill: str,
        width: int,
        dashed: bool,
    ) -> None:
        if not dashed:
            draw.line((screen(start), screen(end)), fill=fill, width=width)
            return
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            return
        pattern = (1.4, 0.9)
        position = 0.0
        draw_on = True
        pattern_index = 0
        while position < length:
            step = min(pattern[pattern_index % len(pattern)], length - position)
            if draw_on:
                a = (
                    start[0] + dx * position / length,
                    start[1] + dy * position / length,
                )
                b = (
                    start[0] + dx * (position + step) / length,
                    start[1] + dy * (position + step) / length,
                )
                draw.line((screen(a), screen(b)), fill=fill, width=width)
            position += step
            draw_on = not draw_on
            pattern_index += 1

    for entity in entities:
        line_width = max(1, round(entity.width * raster_scale / 2.0))
        if entity.kind == "line":
            draw_segment(entity.points[0], entity.points[1], "#111111", line_width, entity.dash)
        elif entity.kind == "polyline":
            for start, end in zip(entity.points, entity.points[1:]):
                draw_segment(start, end, "#111111", line_width, entity.dash)
        elif entity.kind == "circle":
            cx, cy = entity.center
            radius = entity.radius
            box = (
                round((cx - radius) * raster_scale),
                height_px - round((cy + radius) * raster_scale),
                round((cx + radius) * raster_scale),
                height_px - round((cy - radius) * raster_scale),
            )
            if entity.dash:
                steps = max(32, int(2 * math.pi * radius / 0.3))
                for index in range(steps):
                    distance = 2 * math.pi * radius * index / steps
                    if distance % 2.3 < 1.4:
                        a = (cx+radius*math.cos(2*math.pi*index/steps), cy+radius*math.sin(2*math.pi*index/steps))
                        b = (cx+radius*math.cos(2*math.pi*(index+1)/steps), cy+radius*math.sin(2*math.pi*(index+1)/steps))
                        draw_segment(a,b,"#111111",line_width,False)
            else:
                draw.ellipse(box, outline="#111111", width=line_width)
        elif entity.kind == "arc":
            cx, cy = entity.center
            radius = entity.radius
            box = (
                round((cx - radius) * raster_scale),
                height_px - round((cy + radius) * raster_scale),
                round((cx + radius) * raster_scale),
                height_px - round((cy - radius) * raster_scale),
            )
            start, end = _pillow_arc_angles(entity)
            if entity.dash:
                angle, extent = _reportlab_arc_angles(entity)
                length = abs(math.radians(extent)*radius)
                steps = max(16, int(length/0.2))
                for index in range(steps):
                    if (length*index/steps) % 2.3 < 1.4:
                        a = math.radians(angle+extent*index/steps)
                        b = math.radians(angle+extent*(index+1)/steps)
                        draw_segment((cx+radius*math.cos(a),cy+radius*math.sin(a)),
                                     (cx+radius*math.cos(b),cy+radius*math.sin(b)),
                                     "#111111",line_width,False)
            else:
                draw.arc(box, start=start, end=end, fill="#111111", width=line_width)
        elif entity.kind == "text":
            x, y = entity.points[0]
            font_size = max(8, round(entity.height * raster_scale))
            font = _font(font_size)
            value = entity.text or ""
            # Every writer uses a baseline in sheet coordinates. Pillow's
            # default ascender anchor shifted glyphs into table rules.
            advance = font.getlength(value)
            x_px = x*raster_scale
            if entity.align == "center":
                x_px -= advance/2
            elif entity.align == "right":
                x_px -= advance
            draw.text((round(x_px),height_px-round(y*raster_scale)),value,
                      font=font,fill="#111111",anchor="ls")


    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)


def _pillow_arc_angles(entity: Primitive) -> tuple[float, float]:
    start, end = entity.start_angle, entity.end_angle
    # Pillow measures angles clockwise from the positive x direction in the
    # image's downwards-y coordinate system.
    if entity.ccw:
        return -end, -start
    return -start, -end


def _font(size: int):
    from PIL import ImageFont

    candidates = (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)
