"""Regenerate the kernel-free hardware tables from pinned bd_warehouse 0.4.0 CSVs.

Run with an interpreter that has the ``parts`` extra installed:
    .venv/bin/python evals/library/build_component_tables.py

The output is a static snapshot so ``aad_library`` never imports a CAD kernel.
Upstream is a modeling library: these values are not certified copies of paid
standards, which is why records keep the ``source_table`` evidence status.
"""
from __future__ import annotations

import csv
from fractions import Fraction
import json
import math
from pathlib import Path
import re

import bd_warehouse

DATA = Path(bd_warehouse.__file__).parent / "data"
OUT = Path(__file__).resolve().parents[2] / "packages/quality/src/aad_quality/components/standard_tables.json"
NUMBERED_D_IN = {"#0": .06, "#1": .073, "#2": .086, "#3": .099, "#4": .112, "#5": .125, "#6": .138,
                 "#8": .164, "#10": .19, "#12": .216}
# Common inch stock lengths: upstream's ASME ranges start implausibly long (#4-40 from 1 in).
INCH_LENGTHS = (.125, .1875, .25, .3125, .375, .4375, .5, .625, .75, .875, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 3.5, 4, 5, 6)

SCREWS = [
    ("socket_head_cap_parameters", "iso4762", "Socket head cap screw", "ISO 4762", "socket cap allen hex DIN 912 bolt"),
    ("socket_head_cap_parameters", "asme_b18.3", "Socket head cap screw (inch)", "ASME B18.3", "socket cap allen UNC UNF imperial"),
    ("button_head_parameters", "iso7380_1", "Button head screw", "ISO 7380-1", "button socket allen low profile"),
    ("button_head_with_collar_parameters", "iso7380_2", "Flanged button head screw", "ISO 7380-2", "button flange socket allen"),
    ("countersunk_head_parameters", "iso10642", "Countersunk socket screw", "ISO 10642", "flat head csk socket allen DIN 7991"),
    ("countersunk_head_parameters", "iso2009", "Countersunk slotted screw", "ISO 2009", "flat head csk slotted machine DIN 963"),
    ("countersunk_head_parameters", "iso7046", "Countersunk cross-recess screw", "ISO 7046", "flat head csk phillips machine DIN 965"),
    ("countersunk_head_parameters", "iso14581", "Countersunk hexalobular screw", "ISO 14581", "flat head csk torx machine"),
    ("countersunk_head_parameters", "iso14582", "Countersunk high-head hexalobular screw", "ISO 14582", "flat head csk torx"),
    ("raised_countersunk_oval_head_parameters", "iso2010", "Raised countersunk slotted screw", "ISO 2010", "oval head slotted machine"),
    ("raised_countersunk_oval_head_parameters", "iso7047", "Raised countersunk cross-recess screw", "ISO 7047", "oval head phillips machine"),
    ("raised_countersunk_oval_head_parameters", "iso14584", "Raised countersunk hexalobular screw", "ISO 14584", "oval head torx"),
    ("hex_head_parameters", "iso4017", "Hex head screw, fully threaded", "ISO 4017", "hexagon bolt tap DIN 933"),
    ("hex_head_parameters", "iso4014", "Hex head bolt, partly threaded", "ISO 4014", "hexagon bolt DIN 931"),
    ("hex_head_parameters", "din931", "Hex head bolt, partly threaded (DIN)", "DIN 931", "hexagon bolt"),
    ("hex_head_with_flange_parameters", "din1662", "Flanged hex bolt, small series", "DIN 1662", "hexagon flange bolt"),
    ("hex_head_with_flange_parameters", "din1665", "Flanged hex bolt, heavy series", "DIN 1665", "hexagon flange bolt"),
    ("cheese_head_parameters", "iso1207", "Slotted cheese head screw", "ISO 1207", "cheese slotted machine DIN 84"),
    ("cheese_head_parameters", "iso7048", "Cross-recess cheese head screw", "ISO 7048", "cheese phillips machine"),
    ("cheese_head_parameters", "iso14580", "Hexalobular cheese head screw", "ISO 14580", "cheese torx machine"),
    ("pan_head_parameters", "iso1580", "Slotted pan head screw", "ISO 1580", "pan slotted machine DIN 85"),
    ("pan_head_parameters", "iso14583", "Hexalobular pan head screw", "ISO 14583", "pan torx machine"),
    ("pan_head_parameters", "asme_b_18.6.3", "Pan head machine screw (inch)", "ASME B18.6.3", "pan phillips UNC UNF imperial"),
    ("pan_head_with_collar_parameters", "din967", "Cross-recess pan head screw with collar", "DIN 967", "pan flange phillips"),
    ("raised_cheese_head_parameters", "iso7045", "Cross-recess pan head screw", "ISO 7045", "pan phillips machine DIN 7985"),
    ("low_profile_parameters", "OpenBuilds", "Low-profile socket screw", "OpenBuilds", "low profile v-slot"),
    ("shoulder_screw", "iso7379", "Shoulder screw", "ISO 7379", "shoulder stripper bolt pivot"),
    ("setscrew_parameters", "iso4026", "Flat-point socket set screw", "ISO 4026", "grub set screw headless DIN 913"),
]
NUTS = [
    ("hex_nut_parameters", "iso4032", "Hex nut, style 1", "ISO 4032", "hexagon DIN 934"),
    ("hex_nut_parameters", "iso4033", "Hex nut, style 2 (high)", "ISO 4033", "hexagon"),
    ("hex_nut_parameters", "iso4035", "Thin hex nut (jam nut)", "ISO 4035", "hexagon jam lock DIN 439"),
    ("unchamfered_hex_nut_parameters", "iso4036", "Thin unchamfered hex nut", "ISO 4036", "hexagon"),
    ("hex_nut_with_flange_parameters", "din1665", "Flanged hex nut", "DIN 1665", "hexagon flange"),
    ("domed_cap_nut_parameters", "din1587", "Domed cap nut", "DIN 1587", "acorn cap"),
    ("square_nut_parameters", "din557", "Square nut", "DIN 557", "square"),
]
WASHERS = [
    ("plain_washer_parameters", "iso7089", "Plain washer, normal", "ISO 7089", "flat DIN 125"),
    ("plain_washer_parameters", "iso7091", "Plain washer, product grade C", "ISO 7091", "flat DIN 126"),
    ("plain_washer_parameters", "iso7093", "Large plain washer (fender)", "ISO 7093", "flat fender penny DIN 9021"),
    ("plain_washer_parameters", "iso7094", "Extra-large plain washer", "ISO 7094", "flat fender"),
    ("chamfered_washer_parameters", "iso7090", "Chamfered plain washer", "ISO 7090", "flat DIN 125"),
    ("cheese_head_washer_parameters", "iso7092", "Small plain washer", "ISO 7092", "flat DIN 433"),
    ("internal_tooth_lock_washer_parameters", "din6797", "Internal-tooth lock washer", "DIN 6797", "star lock"),
    ("internal_tooth_lock_washer_parameters", "asme_b18.21.1", "Internal-tooth lock washer (inch)", "ASME B18.21.1", "star lock imperial"),
]
NAMES = {"dk": "head_d", "k": "head_h", "m": "height", "s": "across_flats_or_drive", "t": "drive_depth",
         "d1": "inner_d", "d2": "outer_d", "h": "thickness", "dc": "flange_or_collar_d", "c": "flange_thickness",
         "a": "countersink_angle_deg", "rf": "dome_radius", "n": "slot_or_teeth", "recess": "drive", "ds": "shoulder_d",
         "b": "thread_length"}


def rows(name):
    with open(DATA / f"{name}.csv", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        head = [h.strip() for h in next(reader)]
        for row in reader:
            yield dict(zip(head, (cell.strip() for cell in row)))


def number(text):
    try:
        return float(text)
    except ValueError:
        try:
            return float(Fraction(text))
        except (ValueError, ZeroDivisionError):
            return None


def slug(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")


def thread_d_mm(size):
    head = size.split("-")[0]
    if head.startswith("M"):
        return float(head[1:])
    if head in NUMBERED_D_IN:
        return round(NUMBERED_D_IN[head] * 25.4, 3)
    value = number(head)
    return round(value * 25.4, 3) if value else None


def collect(row, std):
    return {key.split(":", 1)[1]: value for key, value in row.items() if key and key.startswith(std + ":") and value}


def dims(values, inch):
    out = {}
    for key, raw in values.items():
        if key in {"short", "long"}:
            continue
        name = NAMES.get(key, key)
        value = number(raw)
        if value is None or key in {"recess"}:
            out[name] = raw
        elif key in {"n", "a"} and name != "slot_or_teeth":
            out[name] = value
        else:
            out[name] = round(value * 25.4, 4) if inch else value
    return out


def holes():
    clear = {row["Size"]: row for row in rows("clearance_hole_sizes")}
    tap = {row["Size"]: row for row in rows("tap_hole_sizes")}
    return clear, tap


def hole_data(size, clear, tap):
    data = {}
    row = clear.get(size) or clear.get(size.split("-")[0])
    if row and size.startswith("M"):
        data["clearance_mm"] = {"close": number(row["Close"]), "normal": number(row["Normal"]), "loose": number(row["Loose"])}
    row = tap.get(size)
    if row and size.startswith("M"):
        data["tap_drill_mm"] = {"soft": number(row["Soft"]), "hard": number(row["Hard"])}
    return data


def lengths(std, size, values, nominal, inch):
    low, high = number(values.get("short", "")), number(values.get("long", ""))
    if std == "asme_b18.3" or std == "asme_b_18.6.3":
        # Series curated by the table author; head dimensions still come from upstream.
        d_in = (thread_d_mm(size) or 0) / 25.4
        return [x for x in INCH_LENGTHS if x >= d_in and x <= max(high or 0, 1, 16 * d_in)]
    options = nominal.get(std, [])
    if low is None or high is None:
        return options
    return [x for x in options if low <= x <= high]


def nominal_lengths():
    table = {}
    for row in rows("nominal_screw_lengths"):
        values = sorted({float(x) for x in row["Nominal_Sizes"].split(",") if x.strip()})
        # Upstream iso1580 has a typo "68" for "6,8".
        if row["Screw_Type"] == "iso1580":
            values = sorted({*(v for v in values if v != 68), 6.0, 8.0})
        table[row["Screw_Type"]] = values
    return table


def base(id_, category, family, title, summary, tags, standard, **extra):
    # Evidence is attached at load time: every row here shares one source.
    return {"id": id_, "category": category, "family": family, "title": title, "summary": summary,
            "tags": tags, "source_type": "standard_series", "standard": standard, **extra}


def main():
    records = []
    nominal = nominal_lengths()
    clear, tap = holes()
    for sheet, std, family, standard, tags in SCREWS:
        for row in rows(sheet):
            size = row.get("Size") or row.get("") or ""
            values = collect(row, std)
            if not size or not values:
                continue
            inch = not size.startswith("M")
            d = thread_d_mm(size)
            info = dims(values, inch)
            options = lengths(std, size, values, nominal, inch)
            if d is None:
                continue
            title = f"{standard} {size} {family}"
            shown = f"{len(options)} stock lengths {options[0]:g}-{options[-1]:g} {'in' if inch else 'mm'}" if options else "length: see supplier"
            head_d = info.get("head_d")
            if head_d is None and isinstance(info.get("across_flats_or_drive"), float) and "hex" in family.lower():
                head_d = round(info["across_flats_or_drive"] / math.cos(math.radians(30)), 3)
            env = {"shape": "screw", "thread_d": d, "head_d": head_d if isinstance(head_d, float) else d,
                   "head_h": info.get("head_h", 0) if isinstance(info.get("head_h", 0), (int, float)) else 0}
            if "ountersunk" in family:
                env["length_includes_head"] = True
            if std == "iso4026":
                env.update(head_d=d, head_h=0, length_includes_head=True)
            if std == "iso7379":
                env["shoulder_d"] = info.get("shoulder_d")
            records.append(base(
                f"fastener.{std.replace('asme_b_18.6.3', 'asme_b18.6.3')}.{slug(size)}", "fastener", family, title,
                f"{size} {family}; {shown}.", f"screw bolt {tags} {size}".split(), standard,
                size=size, thread_d_mm=d, dimensions_mm=info, holes=hole_data(size, clear, tap),
                choices={"length_in" if inch else "length_mm": options}, envelope=env,
            ))
    for sheet, std, family, standard, tags in NUTS:
        for row in rows(sheet):
            values = collect(row, std)
            size = row["Size"]
            if not values:
                continue
            info = dims(values, False)
            records.append(base(
                f"fastener.{std}_nut.{slug(size)}", "fastener", family, f"{standard} {size} {family}",
                f"{size} {family}: {info.get('across_flats_or_drive')} mm across flats, {info.get('height')} mm high.",
                f"nut {tags} {size}".split(), standard, size=size, thread_d_mm=thread_d_mm(size), dimensions_mm=info,
                holes=hole_data(size, clear, tap),
                envelope={"shape": "hex", "af": info.get("across_flats_or_drive"), "h": info.get("height"), "bore": thread_d_mm(size)},
            ))
    for sheet, std, family, standard, tags in WASHERS:
        for row in rows(sheet):
            values = collect(row, std)
            size = row["Size"]
            if not values or "d2" not in values:
                continue
            inch = std.startswith("asme")
            info = dims(values, inch)
            records.append(base(
                f"fastener.{std}.{slug(size)}", "fastener", family, f"{standard} {size} {family}",
                f"{size} {family}: {info['inner_d']} x {info['outer_d']} x {info.get('thickness')} mm.",
                f"washer {tags} {size}".split(), standard, size=size, dimensions_mm=info,
                envelope={"shape": "ring", "d": info["inner_d"], "D": info["outer_d"], "h": info.get("thickness")},
            ))
    for row in rows("metric_set_screw_parameters"):
        pass  # iso4026 above carries the same socket geometry.
    for sheet, family, label, tags in (
        ("single_row_deep_groove_ball_bearing_parameters", "Deep groove ball bearing", "deep_groove", "open radial 60 62 63"),
        ("single_row_capped_deep_groove_ball_bearing_parameters", "Capped deep groove ball bearing", "deep_groove", "shielded sealed ZZ 2Z 2RS radial"),
        ("single_row_angular_contact_ball_bearing_parameters", "Angular contact ball bearing", "angular_contact", "angular thrust radial 72"),
        ("single_row_cylindrical_roller_bearing_parameters", "Cylindrical roller bearing", "cylindrical_roller", "roller NJ radial"),
        ("single_row_tapered_roller_bearing_parameters", "Tapered roller bearing", "tapered_roller", "taper roller 302 303"),
    ):
        for row in rows(sheet):
            # Upstream marks SKF Explorer rows with "*".
            designation = row.get("SKT:Designation", "").replace("*", "").strip()
            d, D = number(row["SKT:d"] or ""), number(row["SKT:D"] or "")
            B = number(row.get("SKT:B", "") or "") if "SKT:T" not in row else number(row["SKT:T"])
            # Upstream uses a dash for rows without a designation, which cannot be bought by name.
            if designation in {"", "-", "–"} or d is None or D is None or B is None:
                continue
            C = number(row.get("SKT:C", "") or "") if "SKT:Cl" not in row else number(row["SKT:Cl"])
            C0 = number(row.get("SKT:C0", "") or "") if "SKT:Cl0" not in row else number(row["SKT:Cl0"])
            specs = {"C_kN": C, "C0_kN": C0, "reference_speed_rpm": number(row.get("SKT:Speed-reference", row.get("SKT:speed-reference", "")) or ""),
                     "limit_speed_rpm": number(row.get("SKT:Speed-limit", row.get("SKT:speed-limit", "")) or "")}
            records.append(base(
                f"bearing.{label}.{slug(designation)}", "bearing", family, f"{designation} {family}",
                f"{designation}: {d:g} x {D:g} x {B:g} mm, C {C} kN.",
                f"bearing ball {tags} {designation} {d:g}x{D:g}x{B:g}".split(), "ISO 15 boundary dimensions",
                designation=designation, manufacturer_reference="SKF designation as tabulated upstream", specs=specs,
                dimensions_mm={"bore_d": d, "outer_D": D, "width_B": B},
                envelope={"shape": "ring", "d": d, "D": D, "h": B},
            ))
    for sheet, family, std, tags in (("din471", "External retaining ring (shaft circlip)", "DIN 471", "circlip snap seeger shaft external"),
                                     ("din472", "Internal retaining ring (bore circlip)", "DIN 472", "circlip snap seeger bore housing internal")):
        for row in rows(sheet):
            d1 = number(row["d1"])
            records.append(base(
                f"retaining.{std.replace(' ', '').lower()}.{d1:g}", "retaining", family, f"{std} {d1:g} mm {family}",
                f"{family} for nominal {d1:g} mm; groove d2 {row['d2']} mm, groove width m {row['m']} mm, ring thickness {row['s']} mm.",
                f"retaining ring {tags} {d1:g}mm".split(), std,
                dimensions_mm={"nominal_d": d1, "thickness_s": number(row["s"]), "groove_d": number(row["d2"]), "groove_width": number(row["m"]), "free_d3": number(row["d3"])},
                envelope={"shape": "ring", "d": min(d1, number(row["d3"])) if sheet == "din472" else number(row["d3"]),
                          "D": max(d1, number(row["d3"])) + 2 * (number(row["a"]) or 0) if sheet == "din472" else d1 + 2 * (number(row["a"]) or 0),
                          "h": number(row["s"])},
            ))
    seen = set()
    for row in rows("shaft_key_parameters"):
        b, h = number(row["din6885:b"]), number(row["din6885:h"])
        if (b, h) in seen:
            continue
        seen.add((b, h))
        shafts = [number(r["Size"]) for r in rows("shaft_key_parameters") if number(r["din6885:b"]) == b]
        records.append(base(
            f"shaft_key.din6885.{b:g}x{h:g}", "power_transmission", "Parallel shaft key", f"DIN 6885 {b:g} x {h:g} parallel key",
            f"Parallel key {b:g} x {h:g} mm for shafts {min(shafts):g}-{max(shafts):g} mm; hub t2 {row['din6885:t2']} mm.",
            f"key keyway parallel shaft {b:g}x{h:g} din6885".split(), "DIN 6885",
            dimensions_mm={"width_b": b, "height_h": h, "hub_depth_t2": number(row["din6885:t2"]), "shaft_depth_t1": number(row["din6885:t4"])},
            specs={"shaft_d_mm": shafts}, envelope={"shape": "box", "x": b, "y": h, "z": 5 * b},
        ))
    static = {row["w"]: row for row in rows("o-ring_static_gland_parameters")}
    for row in rows("o-ring_parameters"):
        idd, w = number(row["iso3601:id"]), number(row["iso3601:w"])
        gland = static.get(row["iso3601:w"])
        records.append(base(
            f"oring.as568.{row['Size']}", "seal", "O-ring (AS568 / ISO 3601-1)", f"AS568-{row['Size']} O-ring {idd:g} x {w:g} mm",
            f"O-ring dash {row['Size']}: {idd:g} mm ID x {w:g} mm cross-section.",
            f"o-ring oring seal gasket as568 iso3601 {row['Size']}".split(), "AS568 / ISO 3601-1",
            dimensions_mm={"inner_d": idd, "cross_section": w},
            specs={"static_groove_width_mm": number(gland["g_w_0"]) if gland else None},
            envelope={"shape": "ring", "d": idd, "D": round(idd + 2 * w, 3), "h": w},
        ))
    for row in rows("heatset_nut_parameters"):
        for vendor in ("McMaster-Carr", "Hilitchi", "ruthex", "CNCKitchen"):
            values = collect(row, vendor)
            if "s" not in values:
                continue
            size = row["Size"]
            part = values.get("part", "").strip()
            info = {k: number(v) for k, v in values.items() if k in {"s", "m", "dc", "material_thickness"}}
            records.append(base(
                f"insert.heatset.{slug(vendor)}.{slug(size)}", "fastener", "Heat-set threaded insert", f"{vendor} {size} heat-set insert",
                f"{size}: OD {info.get('s')} mm, length {info.get('m')} mm, knurl core {info.get('dc')} mm ({vendor}).",
                f"insert heatset threaded plastic brass 3d print {vendor} {size}".split(), "Vendor table",
                source_type_override="manufacturer_part", manufacturer=vendor, mpn=part.split(" ")[0] if part and not part.startswith("http") else None,
                size=size, thread_d_mm=thread_d_mm(size), dimensions_mm={"outer_d": info.get("s"), "length": info.get("m"), "core_d": info.get("dc")},
                specs={"min_material_thickness_mm": info.get("material_thickness"), "drill": values.get("drill")},
                envelope={"shape": "ring", "d": thread_d_mm(size), "D": info.get("s"), "h": info.get("m")},
            ))
    for record in records:
        if record.pop("source_type_override", None):
            record["source_type"] = "manufacturer_part"
    # Upstream repeats a few rows (din1665 nut M12-1.5); the first row wins.
    records = list({r["id"]: r for r in reversed(records)}.values())
    records.sort(key=lambda r: r["id"])
    OUT.write_text(json.dumps(records, separators=(",", ":"), ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    counts = {}
    for record in records:
        counts[record["family"]] = counts.get(record["family"], 0) + 1
    print(len(records), OUT.stat().st_size)
    for key, value in sorted(counts.items()):
        print(f"  {value:4} {key}")


if __name__ == "__main__":
    main()
