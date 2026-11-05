"""Hand-entered mechanical stock: pins, motion, transmission, seals, framing.

Values were entered from trade knowledge of the named series. They carry the
``author_recall`` evidence status unless the evidence file upgrades them, and
``evals/library/check_components.py`` cross-checks bearings against source tables.
"""
from __future__ import annotations

from typing import Any

Record = dict[str, Any]


# Cut to order. The upper bound is a sanity limit; suppliers set their own maximum.
_CUT_LENGTH = {"min": 1, "max": 6000}

def _rec(id_: str, category: str, family: str, title: str, summary: str, tags: str, *, source_type: str = "standard_series", **extra: Any) -> Record:
    return {"id": id_, "category": category, "family": family, "title": title, "summary": summary,
            "tags": tags.split(), "source_type": source_type, **extra}


def _pins() -> list[Record]:
    out: list[Record] = []
    lengths = (4, 5, 6, 8, 10, 12, 14, 16, 18, 20, 24, 30, 36, 40, 50, 60, 80, 100)
    for d in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12, 16, 20):
        options = [x for x in lengths if 2 * d <= x <= max(12 * d, 20)]
        for std, family, tag in (("ISO 8734", "Hardened dowel pin", "m6 hardened"), ("ISO 2338", "Unhardened dowel pin", "m6 h8 soft")):
            out.append(_rec(f"pin.{std.replace(' ', '').lower()}.{d:g}", "pin", family, f"{std} {d:g} mm {family}",
                            f"{d:g} mm {family}; lengths {options[0]}-{options[-1]} mm.", f"pin dowel locating {tag} {d:g}mm", standard=std,
                            dimensions_mm={"d": d}, choices={"length_mm": options}, envelope={"shape": "pin", "d": d}))
    for d in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12):
        options = [x for x in lengths if 2 * d <= x <= 10 * d + 10]
        out.append(_rec(f"pin.iso8752.{d:g}", "pin", "Slotted spring pin (roll pin)", f"ISO 8752 {d:g} mm spring pin",
                        f"{d:g} mm slotted spring pin; lengths {options[0]}-{options[-1]} mm; press into a {d:g} mm H12 hole.",
                        f"pin spring roll slotted din1481 {d:g}mm", standard="ISO 8752", dimensions_mm={"d": d},
                        choices={"length_mm": options}, envelope={"shape": "pin", "d": d}))
    for d, ls in ((1, (8, 10, 12, 16)), (1.6, (12, 16, 20, 25)), (2, (16, 20, 25, 32)), (2.5, (16, 20, 25, 32, 40)),
                  (3.2, (20, 25, 32, 40, 50)), (4, (25, 32, 40, 50, 63)), (5, (32, 40, 50, 63, 80))):
        out.append(_rec(f"pin.iso1234.{d:g}", "pin", "Split cotter pin", f"ISO 1234 {d:g} mm cotter pin",
                        f"{d:g} mm nominal split cotter pin.", f"pin cotter split din94 {d:g}mm", standard="ISO 1234", dimensions_mm={"nominal_d": d},
                        choices={"length_mm": list(ls)}, envelope={"shape": "pin", "d": d}))
    for groove, od, s, for_shaft in ((1.2, 3, .3, "1.4-2"), (1.5, 4, .4, "2-2.5"), (1.9, 5, .5, "2.5-3"), (2.3, 6, .6, "3-4"), (3.2, 7, .6, "4-5"),
                                      (4, 9, .7, "5-7"), (5, 11, .7, "6-8"), (6, 12, .8, "7-9"), (7, 14, .9, "8-11"), (8, 16, 1, "9-12"), (9, 18.5, 1.1, "10-14"), (10, 20, 1.2, "11-15"), (12, 23, 1.3, "13-18"), (15, 29, 1.5, "16-24")):
        out.append(_rec(f"retaining.din6799.{groove:g}", "retaining", "E-clip (lock washer for shafts)", f"DIN 6799 {groove:g} mm E-clip",
                        f"E-clip for {groove:g} mm groove on {for_shaft} mm shaft; OD {od:g} mm, {s:g} mm thick.", f"retaining eclip e-clip shaft din6799 {groove:g}mm", standard="DIN 6799",
                        dimensions_mm={"groove_d": groove, "outer_d": od, "thickness": s}, specs={"shaft_range_mm": for_shaft},
                        envelope={"shape": "ring", "d": groove, "D": od, "h": s}))
    return out


def _extra_fasteners() -> list[Record]:
    out: list[Record] = []
    for size, af, h, d in (("M2", 4, 2.5, 2), ("M2.5", 5, 3.2, 2.5), ("M3", 5.5, 4, 3), ("M4", 7, 5, 4), ("M5", 8, 5, 5), ("M6", 10, 6, 6), ("M8", 13, 8, 8), ("M10", 17, 10, 10), ("M12", 19, 12, 12), ("M16", 24, 16, 16)):
        out.append(_rec(f"fastener.din985.{size}", "fastener", "Nylon-insert lock nut, low", f"DIN 985 {size} nyloc nut",
                        f"{size} nylon-insert lock nut: {af} mm across flats, {h} mm high.", f"nut nyloc nylock lock prevailing torque iso10511 din985 {size}", standard="DIN 985",
                        size=size, dimensions_mm={"across_flats": af, "height": h}, envelope={"shape": "hex", "af": af, "h": h, "bore": d}))
    for size, af, h, d in (("M3", 5.5, 4.5, 3), ("M4", 7, 6, 4), ("M5", 8, 6.8, 5), ("M6", 10, 8, 6), ("M8", 13, 9.5, 8), ("M10", 17, 11.9, 10), ("M12", 19, 16, 12)):
        out.append(_rec(f"fastener.din982.{size}", "fastener", "Nylon-insert lock nut, high", f"DIN 982 {size} nyloc nut",
                        f"{size} high nylon-insert lock nut: {af} mm AF, {h} mm high.", f"nut nyloc nylock lock iso7040 din982 {size}", standard="DIN 982 / ISO 7040",
                        size=size, dimensions_mm={"across_flats": af, "height": h}, envelope={"shape": "hex", "af": af, "h": h, "bore": d}))
    for size, id_, od, s in (("M2", 2.1, 4.4, .5), ("M3", 3.1, 6.2, .8), ("M4", 4.1, 7.6, .9), ("M5", 5.1, 9.2, 1.2), ("M6", 6.1, 11.8, 1.6), ("M8", 8.2, 14.8, 2), ("M10", 10.2, 18.1, 2.2), ("M12", 12.2, 21.1, 2.5)):
        out.append(_rec(f"fastener.din127b.{size}", "fastener", "Spring lock washer", f"DIN 127B {size} spring washer",
                        f"{size} split spring washer: {id_} x {od} x {s} mm.", f"washer spring split lock din127 {size}", standard="DIN 127 B",
                        size=size, dimensions_mm={"inner_d": id_, "outer_d": od, "thickness": s}, envelope={"shape": "ring", "d": id_, "D": od, "h": s}))
    for d, head, grips in ((2.4, 5, "0.5-4"), (3.2, 6.5, "0.5-12"), (4, 8, "1-16"), (4.8, 9.5, "1.5-25"), (6.4, 13, "3-30")):
        out.append(_rec(f"fastener.blind_rivet.{d:g}", "fastener", "Blind (pop) rivet, dome head", f"Blind rivet {d:g} mm dome head",
                        f"{d:g} mm aluminium/steel dome-head blind rivet; head {head} mm; grip range {grips} mm by length; hole {d + .1:g} mm.", f"rivet blind pop iso15977 din7337 {d:g}mm", standard="ISO 15977 / DIN 7337",
                        dimensions_mm={"body_d": d, "head_d": head}, specs={"grip_range_mm": grips, "hole_d_mm": round(d + .1, 2)},
                        choices={"length_mm": [x for x in (4, 5, 6, 8, 10, 12, 14, 16, 20, 25, 30) if x <= 8 * d]}, envelope={"shape": "screw", "thread_d": d, "head_d": head, "head_h": .3 * d}))
    for size, af, h, ls in (("M2", 3.2, None, (5, 6, 8, 10, 12, 15, 20)), ("M2.5", 5, None, (5, 6, 8, 10, 12, 15, 20, 25)), ("M3", 5.5, None, (5, 6, 8, 10, 12, 15, 20, 25, 30, 35, 40, 50)), ("M4", 7, None, (6, 8, 10, 12, 15, 20, 25, 30, 40, 50))):
        d = float(size[1:])
        out.append(_rec(f"fastener.standoff.ff.{size}", "fastener", "Hex standoff, female-female", f"{size} F-F hex standoff",
                        f"{size} brass/nylon/steel threaded standoff, {af} mm AF.", f"standoff spacer pillar hex pcb female {size}", source_type="generic_series",
                        size=size, dimensions_mm={"across_flats": af}, choices={"length_mm": list(ls)}, envelope={"shape": "hex", "af": af, "bore": d}))
        out.append(_rec(f"fastener.standoff.mf.{size}", "fastener", "Hex standoff, male-female", f"{size} M-F hex standoff",
                        f"{size} threaded standoff with 6 mm male stud, {af} mm AF.", f"standoff spacer pillar hex pcb male {size}", source_type="generic_series",
                        size=size, dimensions_mm={"across_flats": af, "stud_length": 6}, choices={"length_mm": list(ls)}, envelope={"shape": "hex", "af": af, "bore": 0}))
    for slot, size, width, length, h in (("6", "M3", 5.8, 10, 3.5), ("6", "M4", 5.8, 10, 3.5), ("6", "M5", 5.8, 10, 3.5), ("8", "M4", 8, 13, 5), ("8", "M5", 8, 13, 5), ("8", "M6", 8, 13, 5), ("10", "M6", 10, 16, 6), ("10", "M8", 10, 18, 6)):
        out.append(_rec(f"fastener.tnut.slot{slot}.{size}", "fastener", "Roll-in T-slot nut", f"{size} T-nut for {slot} mm slot",
                        f"{size} T-nut for {slot} mm slot aluminium extrusion.", f"t-nut tnut hammer extrusion slot v-slot {size} {slot}mm", source_type="generic_series",
                        size=size, dimensions_mm={"width": width, "length": length, "height": h}, specs={"slot_mm": slot}, envelope={"shape": "box", "x": width, "y": length, "z": h}))
    for size in ("ST2.2", "ST2.9", "ST3.5", "ST4.2", "ST4.8", "ST6.3"):
        d = float(size[2:])
        out.append(_rec(f"fastener.iso7049.{size}", "fastener", "Cross-recess pan head self-tapping screw", f"ISO 7049 {size} tapping screw",
                        f"{size} pan head self-tapping screw.", f"screw self-tapping sheet metal din7981 iso7049 {size}", standard="ISO 7049", size=size,
                        dimensions_mm={"thread_d": d}, choices={"length_mm": [x for x in (6.5, 9.5, 13, 16, 19, 25, 32, 38) if x <= 10 * d + 4]},
                        envelope={"shape": "screw", "thread_d": d, "head_d": 2 * d, "head_h": .75 * d}))
    return out


_DEEP_GROOVE = {
    "miniature": ("MR52 2 5 2.5,MR63 3 6 2.5,MR74 4 7 2.5,MR83 3 8 3,MR84 4 8 3,MR85 5 8 2.5,MR95 5 9 3,MR104 4 10 4,MR105 5 10 4,MR106 6 10 3,MR115 5 11 4,MR117 7 11 3,MR126 6 12 4,MR128 8 12 3.5,MR137 7 13 4,MR148 8 14 4,"
                  "683 3 7 3,684 4 9 4,685 5 11 5,686 6 13 5,687 7 14 5,688 8 16 5,689 9 17 5,694 4 11 4,695 5 13 4,696 6 15 5,697 7 17 5,698 8 19 6,699 9 20 6"),
    "60_series": ("603 3 9 5,604 4 12 4,605 5 14 5,606 6 17 6,607 7 19 6,608 8 22 7,609 9 24 7,623 3 10 4,624 4 13 5,625 5 16 5,626 6 19 6,627 7 22 7,629 9 26 8,"
                  "6000 10 26 8,6001 12 28 8,6002 15 32 9,6003 17 35 10,6004 20 42 12,6005 25 47 12,6006 30 55 13,6007 35 62 14,6008 40 68 15,6009 45 75 16,6010 50 80 16"),
    "62_series": "6200 10 30 9,6201 12 32 10,6202 15 35 11,6203 17 40 12,6204 20 47 14,6205 25 52 15,6206 30 62 16,6207 35 72 17,6208 40 80 18,6209 45 85 19,6210 50 90 20",
    "63_series": "6300 10 35 11,6301 12 37 12,6302 15 42 13,6303 17 47 14,6304 20 52 15,6305 25 62 17,6306 30 72 19,6307 35 80 21,6308 40 90 23",
    "thin_section": ("6700 10 15 4,6701 12 18 4,6702 15 21 4,6703 17 23 4,6704 20 27 4,6705 25 32 4,6706 30 37 4,"
                     "6800 10 19 5,6801 12 21 5,6802 15 24 5,6803 17 26 5,6804 20 32 7,6805 25 37 7,6806 30 42 7,6807 35 47 7,6808 40 52 7,"
                     "6900 10 22 6,6901 12 24 6,6902 15 28 7,6903 17 30 7,6904 20 37 9,6905 25 42 9,6906 30 47 9,6907 35 55 10,6908 40 62 12"),
}


def _bearings() -> list[Record]:
    out: list[Record] = []
    for group, text in _DEEP_GROOVE.items():
        for row in text.split(","):
            name, d, D, B = row.split()
            out.append(_rec(f"bearing.radial.{name}", "bearing", "Radial ball bearing (ISO series)", f"{name} ball bearing {d}x{D}x{B} mm",
                            f"{name}: {d} x {D} x {B} mm; order open, ZZ (shielded) or 2RS (sealed). Miniature open versions can be narrower than the shielded width; confirm on the order.", f"bearing ball radial deep groove {group} {name} {name}zz {name}2rs {d}x{D}x{B}", standard="ISO 15 / ISO 355-family",
                            designation=name, dimensions_mm={"bore_d": float(d), "outer_D": float(D), "width_B": float(B)}, choices={"variant": ["open", "ZZ", "2RS"]},
                            envelope={"shape": "ring", "d": float(d), "D": float(D), "h": float(B)}))
    for row in "F623 3 10 4 11.5 1,F624 4 13 5 15 1,F625 5 16 5 18 1,F626 6 19 6 22 1.5,F608 8 22 7 25 1.5,MF63 3 6 2.5 7.2 .6,MF74 4 7 2.5 8.2 .6,MF85 5 8 2.5 9.2 .6,MF105 5 10 4 11.6 .8,MF128 8 12 3.5 13.6 .8,F684 4 9 4 10.3 .6,F688 8 16 5 18 1".split(","):
        name, d, D, B, fd, ft = row.split()
        out.append(_rec(f"bearing.flanged.{name}", "bearing", "Flanged radial ball bearing", f"{name} flanged bearing {d}x{D}x{B} mm",
                        f"{name}: {d} x {D} x {B} mm, flange {fd} x {ft} mm.", f"bearing flanged flange idler {name} {name}zz {d}x{D}x{B}", designation=name,
                        dimensions_mm={"bore_d": float(d), "outer_D": float(D), "width_B": float(B), "flange_d": float(fd), "flange_t": float(ft)},
                        envelope={"shape": "ring", "d": float(d), "D": float(fd), "h": float(B)}))
    for row in "51100 10 24 9,51101 12 26 9,51102 15 28 9,51103 17 30 9,51104 20 35 10,51105 25 42 11,51106 30 47 11,51200 10 26 11,51201 12 28 11,51202 15 32 12,51203 17 35 12,51204 20 40 14,51205 25 47 15,F8-16M 8 16 5,F6-12M 6 12 4.5,F5-10M 5 10 4".split(","):
        name, d, D, H = row.split()
        out.append(_rec(f"bearing.thrust.{name}", "bearing", "Thrust ball bearing", f"{name} thrust bearing {d}x{D}x{H} mm",
                        f"{name}: {d} x {D} x {H} mm axial-load bearing.", f"bearing thrust axial {name} {d}x{D}x{H}", designation=name,
                        dimensions_mm={"bore_d": float(d), "outer_D": float(D), "height": float(H)}, envelope={"shape": "ring", "d": float(d), "D": float(D), "h": float(H)}))
    for row in "HK0306 3 6.5 6,HK0408 4 8 8,HK0509 5 9 9,HK0608 6 10 8,HK0810 8 12 10,HK1010 10 14 10,HK1210 12 16 10,HK1412 14 20 12,HK1512 15 21 12,HK1612 16 22 12,HK2010 20 26 10,HK2512 25 32 12,HK3012 30 37 12".split(","):
        name, d, D, C = row.split()
        out.append(_rec(f"bearing.needle.{name}", "bearing", "Drawn-cup needle roller bearing", f"{name} needle bearing {d}x{D}x{C} mm",
                        f"{name}: runs on a hardened {d} mm shaft, press into {D} mm bore, {C} mm wide.", f"bearing needle drawn cup {name} {d}x{D}x{C}", designation=name,
                        dimensions_mm={"shaft_d": float(d), "outer_D": float(D), "width": float(C)}, envelope={"shape": "ring", "d": float(d), "D": float(D), "h": float(C)}))
    for name, d, D, L in (("LM3UU", 3, 7, 10), ("LM4UU", 4, 8, 12), ("LM5UU", 5, 10, 15), ("LM6UU", 6, 12, 19), ("LM8UU", 8, 15, 24), ("LM10UU", 10, 19, 29), ("LM12UU", 12, 21, 30), ("LM16UU", 16, 28, 37), ("LM20UU", 20, 32, 42), ("LM25UU", 25, 40, 59), ("LM30UU", 30, 45, 64), ("LM8LUU", 8, 15, 45), ("LM12LUU", 12, 21, 57)):
        out.append(_rec(f"bearing.linear.{name}", "bearing", "Linear ball bushing", f"{name} linear bearing {d}x{D}x{L} mm",
                        f"{name}: {d} mm shaft, {D} mm OD, {L} mm long.", f"bearing linear bushing recirculating {name} {d}x{D}x{L}", designation=name,
                        dimensions_mm={"shaft_d": d, "outer_D": D, "length": L}, envelope={"shape": "ring", "d": d, "D": D, "h": L}))
    for d, D in ((3, 5), (4, 6), (5, 7), (6, 8), (8, 10), (10, 12), (12, 14), (14, 16), (15, 17), (16, 18), (20, 23), (25, 28), (30, 34)):
        out.append(_rec(f"bearing.sleeve.ptfe.{d}x{D}", "bearing", "PTFE-composite sleeve bushing (SF-1 type)", f"{d}x{D} mm composite sleeve bushing",
                        f"Metal-polymer sleeve: bore {d} mm, press into {D} mm H7.", f"bushing sleeve plain bearing composite sf-1 du {d}x{D}", source_type="generic_series",
                        dimensions_mm={"bore_d": d, "outer_D": D}, choices={"length_mm": [x for x in (3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30) if x >= d / 2 and x <= 3 * d]},
                        envelope={"shape": "ring", "d": d, "D": D}))
    for name, shaft, kind, x, y, z in (("KP08", 8, "pillow block", 55, 13, 28), ("KFL08", 8, "2-bolt flange", 48, 27, 13), ("KFL000", 10, "2-bolt flange", 60, 36, 13), ("UCP204", 20, "pillow block", 127, 38, 65), ("UCF204", 20, "4-bolt flange", 86, 86, 33), ("UCFL204", 20, "2-bolt flange", 113, 60, 33), ("SK8", 8, "shaft support", 42, 14, 33), ("SK12", 12, "shaft support", 42, 14, 37), ("SHF8", 8, "flange shaft holder", 43, 25, 25), ("SCS8UU", 8, "linear pillow block", 34, 30, 22), ("SCS12UU", 12, "linear pillow block", 42, 36, 28), ("SC16UU", 16, "linear pillow block", 50, 44, 33)):
        out.append(_rec(f"bearing.housed.{name}", "bearing", "Housed bearing unit / shaft support", f"{name} {kind} for {shaft} mm shaft",
                        f"{name} {kind} for {shaft} mm shaft, about {x} x {y} x {z} mm.", f"bearing housed block mount pillow {name} {kind} {shaft}mm", source_type="common_trade_part", designation=name,
                        dimensions_mm={"shaft_d": shaft, "length": x, "width": y, "height": z}, envelope={"shape": "box", "x": x, "y": y, "z": z}))
    return out


def _motion() -> list[Record]:
    out: list[Record] = []
    for name, rail_w, rail_h, h, block_w, block_l, count in (("MGN7C", 7, 4.7, 8, 17, 23, 2), ("MGN7H", 7, 4.7, 8, 17, 30.8, 4), ("MGN9C", 9, 6.5, 10, 20, 28.9, 2), ("MGN9H", 9, 6.5, 10, 20, 39.9, 4), ("MGN12C", 12, 8, 13, 27, 34.7, 4), ("MGN12H", 12, 8, 13, 27, 45.4, 4), ("MGN15C", 15, 10, 16, 32, 42.1, 4), ("MGN15H", 15, 10, 16, 32, 58.8, 4)):
        out.append(_rec(f"linear.rail.{name}", "linear_motion", "Miniature linear guide", f"HIWIN {name} linear guide block",
                        f"{name}: {rail_w} mm rail, block {block_w} x {block_l} mm, {h} mm assembly height.", f"linear rail guide carriage block miniature hiwin {name} mgn", source_type="manufacturer_part", manufacturer="HIWIN", mpn=name,
                        dimensions_mm={"rail_width": rail_w, "rail_height": rail_h, "assembly_height": h, "block_width": block_w, "block_length": block_l},
                        envelope={"shape": "box", "x": block_w, "y": block_l, "z": h}))
    for name, rail_w, h, block_w, block_l in (("HGR15", 15, 28, 34, 61.4), ("HGR20", 20, 30, 44, 77.5), ("HGR25", 23, 40, 48, 84)):
        out.append(_rec(f"linear.rail.{name}", "linear_motion", "Profile linear guide", f"HIWIN {name} profile rail",
                        f"{name}: {rail_w} mm rail, {h} mm assembly height with matching HGH block.", f"linear rail guide carriage profile hiwin {name} hgh", source_type="manufacturer_part", manufacturer="HIWIN", mpn=name,
                        dimensions_mm={"rail_width": rail_w, "assembly_height": h, "block_width": block_w, "block_length": block_l}, envelope={"shape": "box", "x": block_w, "y": block_l, "z": h}))
    for d in (3, 4, 5, 6, 8, 10, 12, 16, 20, 25, 30):
        out.append(_rec(f"shaft.linear.{d}", "linear_motion", "Precision ground shaft (h6)", f"{d} mm h6 hardened shaft",
                        f"{d} mm hardened ground chromed shaft, cut to length.", f"shaft rod linear rail precision ground h6 chrome {d}mm", source_type="generic_series",
                        dimensions_mm={"d": d}, envelope={"shape": "pin", "d": d}, specs={"length": "cut to length"}, choices={"length_mm": _CUT_LENGTH}))
    for name, d, lead, nut_d, nut_l, flange in (("Tr8x2", 8, 2, 22, 15, 22), ("Tr8x8-4start", 8, 8, 22, 15, 22), ("Tr10x2", 10, 2, 25, 25, 42), ("Tr12x3", 12, 3, 28, 35, 48), ("Tr16x4", 16, 4, 36, 44, 48)):
        out.append(_rec(f"linear.leadscrew.{name}", "linear_motion", "Trapezoidal lead screw with nut", f"{name} lead screw",
                        f"{name}: {d} mm screw, {lead} mm/rev lead, flanged brass/POM nut.", f"lead screw acme trapezoidal t8 nut din103 {name}", standard="DIN 103",
                        dimensions_mm={"d": d, "lead": lead, "nut_d": nut_d, "nut_length": nut_l, "flange_d": flange}, envelope={"shape": "pin", "d": d}, choices={"length_mm": _CUT_LENGTH}))
    for name, d, lead, nut_od, nut_l, flange in (("SFU1204", 12, 4, 22, 35, 42), ("SFU1605", 16, 5, 28, 42, 48), ("SFU1610", 16, 10, 28, 57, 48), ("SFU2005", 20, 5, 36, 42, 58), ("SFU2510", 25, 10, 40, 80, 62)):
        out.append(_rec(f"linear.ballscrew.{name}", "linear_motion", "Ball screw with flanged nut", f"{name} ball screw",
                        f"{name}: {d} mm screw, {lead} mm lead, nut OD {nut_od} mm.", f"ball screw ballnut sfu {name}", source_type="common_trade_part", designation=name,
                        dimensions_mm={"d": d, "lead": lead, "nut_od": nut_od, "nut_length": nut_l, "flange_d": flange}, envelope={"shape": "pin", "d": d}, choices={"length_mm": _CUT_LENGTH}))
    for profile, slot, w, h in (("2020", 6, 20, 20), ("2040", 6, 20, 40), ("2060", 6, 20, 60), ("2080", 6, 20, 80), ("3030", 8, 30, 30), ("3060", 8, 30, 60), ("4040", 8, 40, 40), ("4080", 8, 40, 80), ("4545", 10, 45, 45)):
        out.append(_rec(f"framing.extrusion.{profile}", "framing", "T-slot aluminium extrusion", f"{profile} T-slot extrusion ({slot} mm slot)",
                        f"{w} x {h} mm aluminium profile, {slot} mm slot, cut to length.", f"extrusion aluminium t-slot profile frame v-slot 80/20 {profile}", source_type="generic_series",
                        dimensions_mm={"width": w, "height": h, "slot": slot}, envelope={"shape": "box", "x": w, "y": h}, specs={"length": "cut to length"}, choices={"length_mm": _CUT_LENGTH}))
    return out


def _transmission() -> list[Record]:
    out: list[Record] = []
    for teeth, od in ((16, 12), (20, 16), (32, 25), (36, 28), (40, 30), (60, 42.5), (80, 55)):
        for belt, width in (("GT2-6mm", 7), ("GT2-9mm", 10)):
            bores = [b for b in (5, 6.35, 8, 10, 12) if b <= od - 7]
            pitch = round(teeth * 2 / 3.14159, 2)
            out.append(_rec(f"drive.pulley.{belt}.{teeth}T", "power_transmission", "GT2 timing pulley", f"GT2 {teeth}T pulley for {belt[4:]} belt",
                            f"{teeth} tooth 2 mm pitch pulley; pitch diameter {pitch} mm; bores {bores} mm.", f"pulley timing gt2 2gt {teeth}t belt {belt}", source_type="generic_series",
                            dimensions_mm={"pitch_d": pitch, "hub_d": od, "belt_width": width - 1, "pitch": 2}, specs={"teeth": teeth}, choices={"bore_mm": bores}, envelope={"shape": "ring", "d": bores[0] if bores else 5, "D": pitch + 3, "h": width + 8}))
    for name, pitch, widths in (("GT2", 2, (6, 9, 10)), ("GT3", 3, (6, 9, 15)), ("HTD3M", 3, (6, 9, 15)), ("HTD5M", 5, (9, 15, 25)), ("HTD8M", 8, (20, 30)), ("T5", 5, (6, 10, 16)), ("T2.5", 2.5, (4, 6, 10))):
        out.append(_rec(f"drive.belt.{name}", "power_transmission", "Timing belt", f"{name} timing belt, {pitch} mm pitch",
                        f"{name} belt, {pitch} mm pitch; open roll or closed loops in stocked pitch lengths.", f"belt timing synchronous {name} rubber polyurethane", source_type="generic_series",
                        dimensions_mm={"pitch": pitch}, choices={"width_mm": list(widths)}))
    for name, pitch, roller, inner in (("04C/25", 6.35, 3.3, 3.18), ("05B", 8, 5, 3), ("06B", 9.525, 6.35, 5.72), ("08B", 12.7, 8.51, 7.75), ("08A/40", 12.7, 7.92, 7.85), ("10B", 15.875, 10.16, 9.65), ("12B", 19.05, 12.07, 11.68)):
        out.append(_rec(f"drive.chain.{name.replace('/', '_')}", "power_transmission", "Roller chain", f"ISO 606 {name} roller chain",
                        f"Roller chain {name}: pitch {pitch} mm, roller {roller} mm, inner width {inner} mm.", f"chain roller sprocket iso606 ansi {name}", standard="ISO 606", dimensions_mm={"pitch": pitch, "roller_d": roller, "inner_width": inner}))
    for module in (.5, .8, 1, 1.25, 1.5, 2, 2.5, 3):
        out.append(_rec(f"drive.gear.spur.m{module:g}", "power_transmission", "Stock spur gear (20 degree)", f"Module {module:g} stock spur gear",
                        f"Module {module:g} 20 degree involute stock gear; pitch d = {module:g} x teeth; check the vendor's tooth-count list.", f"gear spur involute module m{module:g} pinion din780", standard="DIN 780 modules",
                        specs={"module": module, "pressure_angle_deg": 20}, choices={"teeth": list(range(10, 41)) + [45, 48, 50, 60, 70, 80, 100]}, dimensions_mm={"pitch_d_per_tooth": module}))
    for d1, d2, od, length in ((5, 5, 19, 25), (5, 8, 19, 25), (6.35, 8, 19, 25), (8, 8, 19, 25), (8, 10, 25, 30), (10, 12, 25, 30), (12, 14, 30, 40), (14, 14, 30, 40)):
        out.append(_rec(f"drive.coupling.flexible.{d1:g}x{d2:g}", "power_transmission", "Flexible jaw/helical shaft coupling", f"{d1:g}x{d2:g} mm flexible coupling",
                        f"{d1:g} to {d2:g} mm bore flexible coupling, about OD {od} x {length} mm.", f"coupling coupler flexible shaft helical oldham spider {d1:g}x{d2:g}", source_type="generic_series",
                        dimensions_mm={"bore_1": d1, "bore_2": d2, "outer_d": od, "length": length}, envelope={"shape": "ring", "d": d1, "D": od, "h": length}))
    for d, D, w in ((3, 7, 5), (4, 8, 6), (5, 10, 6), (6, 12, 8), (8, 16, 8), (10, 20, 9), (12, 22, 9), (15, 28, 10), (16, 30, 10), (20, 32, 12), (25, 40, 14), (30, 45, 15)):
        out.append(_rec(f"drive.shaft_collar.{d}", "power_transmission", "Set-screw shaft collar", f"{d} mm shaft collar",
                        f"Shaft collar bore {d} mm, OD {D} mm, {w} mm wide.", f"collar shaft stop din705 set screw {d}mm", standard="DIN 705", dimensions_mm={"bore_d": d, "outer_d": D, "width": w}, envelope={"shape": "ring", "d": d, "D": D, "h": w}))
    return out


def _seals_springs() -> list[Record]:
    out: list[Record] = []
    for row in "6 12 4,8 16 5,8 22 7,10 20 7,12 22 7,15 24 7,15 30 7,17 28 7,20 32 7,20 35 7,25 40 7,25 47 7,30 47 7,30 52 7,35 52 7,35 62 7,40 62 7,40 72 8".split(","):
        d, D, b = map(float, row.split())
        out.append(_rec(f"seal.lip.{d:g}x{D:g}x{b:g}", "seal", "Radial shaft lip seal", f"{d:g}x{D:g}x{b:g} mm lip seal",
                        f"NBR lip seal for {d:g} mm shaft in {D:g} mm bore, {b:g} mm wide.", f"seal lip shaft rotary din3760 tc oil {d:g}x{D:g}x{b:g}", standard="DIN 3760 / ISO 6194",
                        dimensions_mm={"shaft_d": d, "bore_D": D, "width": b}, envelope={"shape": "ring", "d": d, "D": D, "h": b}))
    for D, d, t, h0 in ((8, 4.2, .4, .6), (10, 5.2, .5, .75), (12.5, 6.2, .7, 1), (16, 8.2, .9, 1.25), (20, 10.2, 1.1, 1.55), (25, 12.2, 1.5, 2.05), (31.5, 16.3, 1.75, 2.45), (40, 20.4, 2.25, 3.15), (50, 25.4, 3, 4.1)):
        out.append(_rec(f"spring.disc.din2093.{D:g}x{d:g}x{t:g}", "spring", "Disc (Belleville) spring", f"DIN 2093 {D:g}x{d:g}x{t:g} disc spring",
                        f"Disc spring OD {D:g}, ID {d:g}, thickness {t:g}, free height {h0:g} mm.", f"spring disc belleville din2093 preload {D:g}x{d:g}", standard="DIN 2093",
                        dimensions_mm={"outer_d": D, "inner_d": d, "thickness": t, "free_height": h0}, envelope={"shape": "ring", "d": d, "D": D, "h": h0}))
    for wire, od, length in ((.3, 3, 10), (.3, 4, 15), (.5, 4, 10), (.5, 5, 20), (.5, 6, 25), (.6, 6, 15), (.8, 6, 20), (.8, 8, 30), (1, 8, 25), (1, 10, 30), (1.2, 10, 40), (1.5, 12, 50), (2, 16, 50)):
        out.append(_rec(f"spring.compression.{wire:g}x{od:g}x{length:g}", "spring", "Compression spring (stock assortment size)", f"{wire:g} wire {od:g} OD x {length:g} mm compression spring",
                        f"Music-wire compression spring: wire {wire:g} mm, OD {od:g} mm, free length {length:g} mm. Get rate from the actual supplier.", f"spring compression coil music wire {wire:g}x{od:g}x{length:g}", source_type="generic_series",
                        dimensions_mm={"wire_d": wire, "outer_d": od, "free_length": length}, envelope={"shape": "ring", "d": od - 2 * wire, "D": od, "h": length}))
    for d, t in ((3, 1), (5, 1), (5, 2), (6, 2), (6, 3), (8, 2), (8, 3), (10, 2), (10, 3), (10, 5), (12, 3), (15, 3), (15, 5), (20, 3), (20, 5), (25, 5)):
        out.append(_rec(f"magnet.disc.N35.{d}x{t}", "magnet", "NdFeB disc magnet", f"N35 {d}x{t} mm disc magnet",
                        f"NdFeB disc magnet {d} mm dia x {t} mm, axially magnetised, Ni plated; N35-N52 grades.", f"magnet neodymium ndfeb disc n35 n52 {d}x{t}", source_type="generic_series",
                        dimensions_mm={"d": d, "thickness": t}, envelope={"shape": "cylinder", "d": d, "h": t}))
    return out


def _fluid_cable() -> list[Record]:
    out: list[Record] = []
    for tube in (4, 6, 8, 10, 12):
        threads = {4: ("M5", "M6", "G1/8"), 6: ("M5", "G1/8", "G1/4"), 8: ("G1/8", "G1/4", "G3/8"), 10: ("G1/4", "G3/8"), 12: ("G1/4", "G3/8", "G1/2")}[tube]
        out.append(_rec(f"pneumatic.push_fitting.{tube}", "fluid", "Pneumatic push-in fitting", f"{tube} mm push-in fitting (PC{tube})",
                        f"Straight push-to-connect fitting for {tube} mm tube; threads {', '.join(threads)}.", f"pneumatic fitting push connect pc{tube} tube air {tube}mm", source_type="generic_series",
                        dimensions_mm={"tube_od": tube}, choices={"thread": list(threads)}))
    for name, thread, cable, hole, nut in (("PG7", "PG7", "3-6.5", 12.5, 19), ("PG9", "PG9", "4-8", 15.2, 22), ("PG11", "PG11", "5-10", 18.6, 24), ("PG13.5", "PG13.5", "6-12", 20.4, 27), ("PG16", "PG16", "10-14", 22.5, 30), ("M12", "M12x1.5", "3-6.5", 12, 15), ("M16", "M16x1.5", "4-8", 16, 19), ("M20", "M20x1.5", "6-12", 20, 24), ("M25", "M25x1.5", "13-18", 25, 30)):
        out.append(_rec(f"cable.gland.{name.replace('.', '_')}", "cable_management", "IP68 nylon cable gland", f"{name} cable gland",
                        f"{thread} gland for {cable} mm cable; panel hole {hole} mm.", f"cable gland ip68 nylon strain relief {name}", source_type="generic_series",
                        dimensions_mm={"panel_hole_d": hole, "nut_across_flats": nut}, specs={"cable_range_mm": cable}))
    for awg, od_mm, area, amps in ((30, .25, .05, .5), (28, .32, .08, 1), (26, .4, .13, 1.5), (24, .51, .2, 3), (22, .64, .33, 5), (20, .81, .52, 7), (18, 1.02, .82, 10), (16, 1.29, 1.31, 13), (14, 1.63, 2.08, 17), (12, 2.05, 3.31, 23), (10, 2.59, 5.26, 33), (8, 3.26, 8.37, 46)):
        out.append(_rec(f"wire.awg.{awg}", "wire", "Stranded copper hookup wire", f"AWG {awg} wire ({area} mm2)",
                        f"AWG {awg}: {area} mm2, conductor about {od_mm} mm; rule-of-thumb chassis current {amps} A; add insulation to the OD.", f"wire cable awg {awg} hookup copper stranded silicone {area}mm2", standard="ASTM B258",
                        dimensions_mm={"conductor_d": od_mm}, specs={"area_mm2": area, "indicative_chassis_current_A": amps}))
    for w, l in ((2.5, 100), (2.5, 200), (3.6, 150), (4.8, 200), (4.8, 300), (7.6, 370)):
        out.append(_rec(f"cable.tie.{w:g}x{l}", "cable_management", "Nylon cable tie", f"{w:g} x {l} mm cable tie", f"Nylon cable tie {w:g} x {l} mm.", f"cable zip tie nylon {w:g}x{l}", source_type="generic_series", dimensions_mm={"width": w, "length": l}))
    for d in (1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12, 16, 20):
        out.append(_rec(f"cable.heatshrink.{d:g}", "cable_management", "Heat-shrink tubing 2:1", f"{d:g} mm heat-shrink", f"{d:g} mm pre-shrink tube, shrinks 2:1.", f"heat shrink heatshrink tube sleeving 2:1 {d:g}mm", source_type="generic_series", dimensions_mm={"pre_shrink_d": d, "post_shrink_d": d / 2}))
    for name, blade, awg in (("Faston-2.8", 2.8, "22-18"), ("Faston-4.8", 4.8, "22-16"), ("Faston-6.3", 6.3, "22-14")):
        out.append(_rec(f"terminal.quick_connect.{name}", "connector", "Blade quick-connect terminal", f"{blade} mm blade terminal", f"{blade} x 0.8 mm tab/receptacle, AWG {awg}.", f"terminal spade faston quick connect blade {blade}mm din46244", standard="DIN 46244 / IEC 60760", dimensions_mm={"blade_width": blade, "blade_thickness": .8}))
    for name, conductors in (("221-412", 2), ("221-413", 3), ("221-415", 5)):
        out.append(_rec(f"terminal.wago.{name}", "connector", "Lever splicing connector", f"WAGO {name} lever connector", f"{conductors}-conductor lever splice, 0.14-4 mm2, 32 A rated.", f"wago lever connector splice 221 {name}", source_type="manufacturer_part", manufacturer="WAGO", mpn=name,
                        dimensions_mm={"width": 6.3 * conductors + 1.7, "depth": 18.3, "height": 8.3}, envelope={"shape": "box", "x": 6.3 * conductors + 1.7, "y": 18.3, "z": 8.3}))
    return out


def records() -> list[Record]:
    return [*_pins(), *_extra_fasteners(), *_bearings(), *_motion(), *_transmission(), *_seals_springs(), *_fluid_cable()]
