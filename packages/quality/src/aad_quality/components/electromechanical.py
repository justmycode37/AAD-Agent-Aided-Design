"""Hand-entered motors, connectors, switches, power parts and dev boards."""
from __future__ import annotations

from typing import Any

Record = dict[str, Any]


def _rec(id_: str, category: str, family: str, title: str, summary: str, tags: str, *, source_type: str = "manufacturer_part", **extra: Any) -> Record:
    return {"id": id_, "category": category, "family": family, "title": title, "summary": summary, "tags": tags.split(), "source_type": source_type, **extra}


def _safe(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)


def _box(x: float, y: float, z: float) -> dict[str, Any]:
    return {"shape": "box", "x": x, "y": y, "z": z}


def _motors() -> list[Record]:
    out: list[Record] = []
    # name, maker, frame, body length, current A, holding torque Nm, shaft d, shaft length, hole spacing, pilot d
    steppers = (
        ("8HS11-0204S", "StepperOnline", 20.3, 30, .2, .016, 4, 15, 16, 16),
        ("11HS20-0674S", "StepperOnline", 28.2, 51, .67, .095, 5, 20, 23, 22),
        ("14HS10-0404S", "StepperOnline", 35.2, 28, .4, .14, 5, 20, 26, 22),
        ("17HS08-1004S", "StepperOnline", 42.3, 20, 1, .13, 5, 20, 31, 22),
        ("17HS10-0704S", "StepperOnline", 42.3, 25, .7, .18, 5, 24, 31, 22),
        ("17HS13-0404S", "StepperOnline", 42.3, 34, .4, .26, 5, 24, 31, 22),
        ("17HS15-1504S", "StepperOnline", 42.3, 38, 1.5, .45, 5, 24, 31, 22),
        ("17HS19-2004S1", "StepperOnline", 42.3, 48, 2, .59, 5, 24, 31, 22),
        ("17HS24-2104S", "StepperOnline", 42.3, 60, 2.1, .65, 5, 24, 31, 22),
        ("17HS4401", "Generic NEMA 17", 42.3, 40, 1.5, .40, 5, 21, 31, 22),
        ("23HS22-2804S", "StepperOnline", 57, 56, 2.8, 1.26, 6.35, 21, 47.14, 38.1),
        ("23HS30-2804S", "StepperOnline", 57, 76, 2.8, 1.89, 8, 21, 47.14, 38.1),
        ("23HS45-4204S", "StepperOnline", 57, 112, 4.2, 3, 8, 21, 47.14, 38.1),
        ("34HS31-6004S", "StepperOnline", 86, 80, 6, 4.5, 14, 30, 69.6, 73),
    )
    for name, maker, frame, length, amps, torque, shaft, shaft_l, holes, pilot in steppers:
        nema = {20.3: 8, 28.2: 11, 35.2: 14, 42.3: 17, 57: 23, 86: 34}[frame]
        out.append(_rec(f"motor.stepper.{_safe(name)}", "motor", f"NEMA {nema} stepper motor", f"{name} NEMA {nema} stepper",
                        f"{name}: {frame} mm frame x {length} mm, {amps} A, {torque} Nm, {shaft} mm shaft.", f"motor stepper nema{nema} nema {nema} bipolar {name}", manufacturer=maker, mpn=name, standard=f"NEMA ICS 16 frame {nema}",
                        specs={"rated_current_a": amps, "holding_torque_nm": torque, "step_angle_deg": 1.8},
                        dimensions_mm={"frame": frame, "body_length": length, "shaft_d": shaft, "shaft_length": shaft_l, "mount_hole_spacing": holes, "pilot_d": pilot},
                        envelope={"shape": "motor", "x": frame, "y": frame, "z": length, "shaft_d": shaft, "shaft_l": shaft_l, "pilot_d": pilot, "pilot_h": 2}))
    for name, d, length, volts, gear, shaft in (("28BYJ-48", 28, 19, 5, "1:64 gearbox", 5),):
        out.append(_rec(f"motor.stepper.{name}", "motor", "Geared unipolar stepper", f"{name} {volts} V geared stepper", f"{name}: {d} mm x {length} mm, {volts} or 12 V unipolar, {gear}; 5 mm flat shaft offset 8 mm.", f"motor stepper geared unipolar uln2003 {name}", source_type="common_trade_part", designation=name,
                        specs={"voltage_v": volts, "gear_ratio": "about 1:64"}, dimensions_mm={"d": d, "h": length, "shaft_d": shaft, "shaft_offset": 8, "mount_hole_spacing": 35}, envelope={"shape": "motor", "d": d, "z": length, "shaft_d": shaft, "shaft_l": 10}))
    brushed = (("FA-130", 20.1, 15.1, 25, 2, 1.5, 3), ("RE-140", 20.1, 15.1, 25, 2, 3, 3), ("RS-280", 24, 24, 30, 2, 6, 6), ("RS-385", 27.7, 27.7, 38, 2.3, 12, 12), ("RS-390", 27.7, 27.7, 38, 3.17, 12, 12), ("RS-540", 36, 36, 50, 3.17, 7.2, 12), ("RS-550", 36, 36, 57, 3.17, 12, 18), ("RS-775", 42, 42, 67, 5, 12, 24), ("RS-795", 45, 45, 67, 5, 12, 24))
    for name, x, y, l, shaft, vmin, vmax in brushed:
        out.append(_rec(f"motor.brushed.{name}", "motor", "Brushed DC can motor", f"{name} brushed DC motor", f"{name}: about {x} x {y} x {l} mm body, {shaft} mm shaft, {vmin:g}-{vmax:g} V. Speed and torque vary by winding vendor.", f"motor dc brushed can {name} mabuchi", source_type="common_trade_part", designation=name, specs={"voltage_range_v": [vmin, vmax]}, dimensions_mm={"x": x, "y": y, "body_length": l, "shaft_d": shaft}, envelope={"shape": "motor", "d": x, "z": l, "shaft_d": shaft, "shaft_l": 8}))
    for ratio in (10, 30, 50, 100, 150, 250, 298, 1000):
        out.append(_rec(f"motor.gearmotor.n20.{ratio}", "motor", "N20 micro metal gearmotor", f"N20 {ratio}:1 gearmotor", f"N20: 12 x 10 mm cross section, 25 mm long, 3 mm D shaft, {ratio}:1 gears, 3-12 V windings.", f"motor gearmotor n20 micro metal geared pololu {ratio}:1", source_type="common_trade_part", designation=f"N20 {ratio}:1", specs={"gear_ratio": ratio, "voltage_range_v": [3, 12]}, dimensions_mm={"width": 12, "height": 10, "length": 25, "shaft_d": 3, "shaft_length": 10}, envelope=_box(12, 10, 25)))
    for name, d, l in (("6x15-coreless", 6, 15), ("7x16-coreless", 7, 16), ("8520-coreless", 8.5, 20), ("1020-coreless", 10, 20)):
        out.append(_rec(f"motor.coreless.{name}", "motor", "Coreless brushed micro motor", f"{name} micro motor", f"Coreless {d} x {l} mm, 3.7 V nominal.", f"motor coreless micro drone brushed {name}", source_type="common_trade_part", designation=name, dimensions_mm={"d": d, "length": l, "shaft_d": 1}, envelope={"shape": "motor", "d": d, "z": l, "shaft_d": 1, "shaft_l": 5}))
    for name, maker, kv, d, h, shaft, mount, cells in (
        ("MT2204-2300KV", "T-Motor", 2300, 27.9, 28, 3, "M3 16/19 mm", "2-3S"), ("MN2212-920KV", "T-Motor", 920, 27.5, 26.5, 4, "M3 16/19 mm", "2-4S"),
        ("MN4006-380KV", "T-Motor", 380, 44.6, 28, 4, "M3 25/30 mm", "4-6S"), ("U8-100KV", "T-Motor", 100, 87, 43, 8, "M4 30/40 mm", "6-12S"),
        ("F80-1900KV", "T-Motor", 1900, 27.9, 33.5, 5, "M3 16/19 mm", "2-4S"), ("D2830-1000KV", "Turnigy", 1000, 28, 30, 3, "M3 25 mm", "2-3S"),
        ("XING2-2207-1855KV", "iFlight", 1855, 27.5, 32.5, 5, "M3 16x16 mm", "6S"), ("2205-2300KV", "Generic", 2300, 27.9, 30, 5, "M3 16/19 mm", "3-4S"), ("D5065-270KV", "ODrive Robotics", 270, 63.5, 56, 8, "M4 44 mm", "6-8S"),
    ):
        out.append(_rec(f"motor.bldc.{_safe(name)}", "motor", "Brushless outrunner motor", f"{maker} {name} BLDC motor", f"{name}: {d} mm bell, {h} mm high, {shaft} mm shaft, {cells}, mount {mount}.", f"motor bldc brushless outrunner drone esc {name} {maker} {kv}kv", manufacturer=maker, mpn=name, specs={"kv": kv, "cells": cells}, dimensions_mm={"d": d, "h": h, "shaft_d": shaft}, envelope={"shape": "motor", "d": d, "z": h, "shaft_d": shaft, "shaft_l": 10}))
    for name, x, y, z, torque, kind, volts in (
        ("SG90", 23.1, 12.2, 31, .157, "plastic gear; clone outlines vary, measure before fitting", "4.8-6"), ("MG90S", 22.8, 12.2, 28.5, .22, "metal gear", "4.8-6"), ("ES08MA II", 23, 11.5, 24, .2, "metal gear", "4.8-6"),
        ("MG995", 40.7, 19.7, 42.9, .9, "metal gear", "4.8-7.2"), ("MG996R", 40.7, 19.7, 42.9, 1.08, "metal gear", "4.8-6.6"), ("DS3218", 40, 20, 40.5, 2, "metal gear digital", "4.8-6.8"), ("FS5106B", 40.8, 20.1, 38, .6, "plastic gear", "4.8-6"), ("HS-422", 40.6, 19.8, 36.6, .4, "plastic gear", "4.8-6"), ("XL430-W250-T", 28.5, 34, 46.5, 1.4, "smart servo; stall quoted at 11.1 V", "6.5-12"), ("XM430-W350-T", 28.5, 34, 46.5, 4.1, "smart servo", "10-14.8"),
    ):
        maker = "ROBOTIS" if name.startswith("X") else ("Hitec" if name.startswith("HS") else ("Tower Pro" if name.startswith(("SG", "MG")) else "Common trade part"))
        out.append(_rec(f"motor.servo.{_safe(name)}", "motor", "RC or smart servo", f"{name} servo", f"{name}: {x} x {y} x {z} mm, stall about {torque} Nm, {volts} V, {kind}.", f"servo rc hobby actuator {name} dynamixel {kind}", manufacturer=maker, mpn=name, specs={"stall_torque_nm_nominal": torque, "voltage_v": volts}, dimensions_mm={"x": x, "y": y, "z": z}, envelope=_box(x, y, z)))
    for name, maker, v, x, y, z in (
        ("NF-A4x10 FLX", "Noctua", 12, 40, 40, 11), ("NF-A4x10 5V", "Noctua", 5, 40, 40, 11), ("NF-A4x20 FLX", "Noctua", 12, 40, 40, 20), ("NF-A6x25 PWM", "Noctua", 12, 60, 60, 25), ("NF-A8 PWM", "Noctua", 12, 80, 80, 25), ("NF-A9x14 PWM", "Noctua", 12, 92, 92, 14), ("NF-A12x25 PWM", "Noctua", 12, 120, 120, 25), ("NF-A14 PWM", "Noctua", 12, 140, 140, 25),
        ("MF40100V2-1000U-A99", "Sunon", 12, 40, 40, 10), ("MF50151VX-1000C-A99", "Sunon", 12, 50, 50, 15), ("EF92251S1-Q010-S99", "Sunon", 12, 92, 92, 25), ("AFB0405HA", "Delta", 5, 40, 40, 10), ("EFB0412HA", "Delta", 12, 40, 40, 10),
    ):
        out.append(_rec(f"motor.fan.{_safe(name)}", "motor", "DC axial fan", f"{maker} {name} fan", f"{name}: {x} x {y} x {z} mm {v} V DC axial fan.", f"fan axial cooling dc {x}mm {x}x{z} {name} {maker}", manufacturer=maker, mpn=name, specs={"voltage_v": v}, dimensions_mm={"x": x, "y": y, "z": z, "hole_spacing": {40: 32, 50: 40, 60: 50, 80: 71.5, 92: 82.5, 120: 105, 140: 124.5}[x], "hole_d": 4.3}, envelope=_box(x, y, z)))
    for name, x, y, z in (("TEC1-12706", 40, 40, 3.9), ("TEC1-12710", 40, 40, 3.9), ("TEC1-12703", 40, 40, 3.9)):
        out.append(_rec(f"thermal.peltier.{name}", "thermal", "Peltier thermoelectric module", f"{name} Peltier module", f"{name}: {x} x {y} x {z} mm 12 V thermoelectric cooler.", f"peltier thermoelectric cooler tec {name}", source_type="common_trade_part", designation=name, dimensions_mm={"x": x, "y": y, "z": z}, envelope=_box(x, y, z)))
    for x, y, z in ((8.5, 8.5, 2.5), (9, 9, 5), (20, 20, 6), (40, 40, 11), (60, 60, 10)):
        out.append(_rec(f"thermal.heatsink.{x:g}x{y:g}x{z:g}", "thermal", "Aluminium finned heatsink", f"{x:g} x {y:g} x {z:g} mm heatsink", "Stock adhesive or screw-mount finned aluminium heatsink.", f"heatsink thermal aluminium fins {x:g}x{y:g}", source_type="generic_series", dimensions_mm={"x": x, "y": y, "z": z}, envelope=_box(x, y, z)))
    return out


# name, manufacturer, pitch, rows (name_template, ways), mating, x/y/z per way formula
_HEADERS = (
    ("JST-XH", "JST", 2.5, "B{n}B-XH-A", range(2, 13), 2.5, 5.75, 7, "Connector_JST:JST_XH", "Wire-to-board, 3 A, THT header; housing XHP-{n}"),
    ("JST-PH", "JST", 2, "B{n}B-PH-K-S", range(2, 11), 2, 4.5, 6, "Connector_JST:JST_PH", "Wire-to-board, 2 A, THT header; housing PHR-{n}"),
    ("JST-PH-SMD", "JST", 2, "S{n}B-PH-SM4-TB", range(2, 11), 2, 7.9, 5.5, "Connector_JST:JST_PH", "Wire-to-board, 2 A, SMD side-entry"),
    ("JST-ZH", "JST", 1.5, "B{n}B-ZR", range(2, 11), 1.5, 3.5, 7, "Connector_JST:JST_ZH", "Wire-to-board, 1 A, THT header; housing ZHR-{n}"),
    ("JST-SH", "JST", 1, "BM{n:02d}B-SRSS-TB", range(2, 11), 1, 2.9, 4.25, "Connector_JST:JST_SH", "Wire-to-board 1 A; Qwiic/STEMMA QT uses 4 way"),
    ("JST-GH", "JST", 1.25, "BM{n:02d}B-GHS-TBT", range(2, 11), 1.25, 3.5, 4.1, "Connector_JST:JST_GH", "Locking wire-to-board, Pixhawk standard"),
    ("Molex-KK254", "Molex", 2.54, "22-27-2{n:02d}1", range(2, 13), 2.54, 6.35, 8.9, "Connector_Molex:Molex_KK-254", "Friction-lock wire-to-board, 2.54 mm"),
    ("Molex-MicroFit", "Molex", 3, "43045-{n:02d}12", (2, 4, 6, 8, 10, 12), 1.5, 9, 9.3, "Connector_Molex:Molex_Micro-Fit_3.0_43045", "8.5 A dual-row power connector"),
)


def _connectors() -> list[Record]:
    out: list[Record] = []
    for family, maker, pitch, template, ways, per_way, y, z, footprint, summary in _HEADERS:
        for n in ways:
            mpn = template.format(n=n)
            x = round((n - 1) * per_way + 4.9, 2)
            out.append(_rec(f"connector.{family}.{n}pin", "connector", f"{family} connector", f"{mpn} {family} {n} way", f"{mpn}: {n} way, {pitch} mm pitch. {summary}. About {x} x {y} x {z} mm.", f"connector header wire-to-board {family} {pitch}mm {n}pin {mpn}", manufacturer=maker, mpn=mpn, kicad_footprint_hint=footprint, specs={"pitch_mm": pitch, "ways": n}, dimensions_mm={"x": x, "y": y, "z": z}, envelope=_box(x, y, z)))
    for n in range(1, 41):
        for row in (1, 2):
            if n > 20 and row == 1 and n % 5:
                continue
            ways = n * row
            if ways > 80 or (n > 20 and n != 40):
                continue
            out.append(_rec(f"connector.pinheader.2.54.{row}x{n}", "connector", "2.54 mm pin header", f"{row}x{n} 2.54 mm pin header", f"{row} row, {n} way 0.1 inch header; 11.5 mm typical length, 2.54 mm plastic body.", f"connector pin header dupont gpio 2.54mm 0.1in {row}x{n}", source_type="generic_series", specs={"pitch_mm": 2.54, "rows": row, "ways": ways}, dimensions_mm={"x": 2.54 * n, "y": 2.54 * row, "z": 11.5}, envelope=_box(2.54 * n, 2.54 * row, 11.5)))
    for pitch, y, z in ((5.08, 7.6, 10), (3.5, 6.5, 8.5), (2.54, 6, 8)):
        for n in (2, 3, 4, 6):
            x = round(n * pitch, 2)
            out.append(_rec(f"connector.screw_terminal.{pitch}.{n}pin", "connector", "PCB screw terminal", f"{pitch} mm {n} way screw terminal", f"{n} way PCB screw terminal, {pitch} mm pitch.", f"connector screw terminal block pcb phoenix kf {pitch}mm {n}pin", source_type="generic_series", specs={"pitch_mm": pitch, "ways": n}, dimensions_mm={"x": x, "y": y, "z": z}, envelope=_box(x, y, z)))
    trade = (
        ("USB-C-16P", "connector", "USB-C receptacle (16 pin)", 8.94, 7.35, 3.26, "HRO TYPE-C-31-M-12", "HRO", "Mid-mount SMD USB 2.0 Type-C receptacle"),
        ("USB-Micro-B", "connector", "Micro USB B receptacle", 7.5, 5, 2.8, "10118194-0001LF", "Amphenol", "SMD micro-USB receptacle"),
        ("USB-A", "connector", "USB-A receptacle", 14, 17, 7, "292303-1", "TE Connectivity", "THT USB A receptacle"),
        ("RJ45-MagJack", "connector", "RJ45 with magnetics", 16, 21.3, 13.5, "HR911105A", "HanRun", "RJ45 with integrated magnetics"),
        ("DC-Barrel-5.5x2.1", "connector", "DC barrel jack", 9, 14.4, 11, "PJ-002A", "CUI / Same Sky", "5.5 x 2.1 mm PCB jack"),
        ("SMA-edge", "connector", "SMA RF connector", 6.35, 11, 6.35, "SMA-J-P-H-ST-EM1", "Samtec", "50 ohm edge-mount SMA"),
        ("U.FL", "connector", "U.FL RF receptacle", 3, 2.6, 1.25, "U.FL-R-SMT-1", "Hirose", "SMD RF micro coaxial"),
        ("MicroSD", "connector", "microSD socket", 11.5, 14.5, 1.8, "DM3AT-SF-PEJM5", "Hirose", "Push-push microSD socket"),
        ("XT30", "connector", "Amass XT30 power connector", 10, 5, 16, "XT30U-F", "Amass", "15-20 A continuous battery connector"),
        ("XT60", "connector", "Amass XT60 power connector", 15.6, 8.1, 23.5, "XT60H-F", "Amass", "30 A continuous, 60 A peak battery connector"),
        ("XT90", "connector", "Amass XT90 power connector", 21.5, 10.8, 30, "XT90H-F", "Amass", "Battery connector named for its 90 A peak; check the continuous rating"),
        ("MR60", "connector", "Amass MR60 three-pole connector", 16, 9, 23.5, "MR60-F", "Amass", "30 A three-phase motor connector"),
        ("Deutsch-DT04-2P", "connector", "Deutsch DT sealed connector", 22, 23, 44, "DT04-2P", "TE / Deutsch", "IP68 2 way 13 A automotive connector"),
        ("M12-A-coded", "connector", "M12 sensor connector", 12, 12, 40, "M12 A-coded 4P", "Generic", "Industrial M12 4 or 5 way sensor connector"),
        ("IEC-C14", "connector", "IEC C14 mains inlet", 27, 19.8, 31, "PX0580/63", "Bulgin", "10 A IEC panel inlet"),
    )
    for slug, category, family, x, y, z, mpn, maker, summary in trade:
        out.append(_rec(f"connector.{slug}", category, family, f"{mpn} {family}", f"{mpn}: {summary}. About {x} x {y} x {z} mm.", f"connector {slug} {family} {mpn} {maker}", manufacturer=maker, mpn=mpn, dimensions_mm={"x": x, "y": y, "z": z}, envelope=_box(x, y, z)))
    for ways in (10, 20, 26, 40):
        out.append(_rec(f"connector.idc.2x{ways // 2}", "connector", "Boxed IDC header", f"{ways} way boxed IDC header", f"2 row {ways} way 2.54 mm shrouded IDC header.", f"connector idc ribbon box header 2.54 {ways}", source_type="generic_series", specs={"pitch_mm": 2.54, "ways": ways}, dimensions_mm={"x": 2.54 * ways / 2 + 7.6, "y": 8.9, "z": 9}, envelope=_box(2.54 * ways / 2 + 7.6, 8.9, 9)))
    return out


def _switching() -> list[Record]:
    out: list[Record] = []
    rows = (
        ("switch", "Tactile switch", "PTS645SM43SMTR92 LFS", "C&K", 6, 6, 4.3, "6 x 6 mm SMD tactile"), ("switch", "Tactile switch", "B3F-1000", "Omron", 6, 6, 4.3, "6 x 6 mm THT tactile"), ("switch", "Tactile switch", "B3U-1000P", "Omron", 3, 2.5, 1.6, "Ultra-small SMD tactile"), ("switch", "Tactile switch", "TL3301AF160QG", "E-Switch", 6.2, 6.2, 3.1, "6 mm SMD tactile"),
        ("switch", "Microswitch", "D2F-01", "Omron", 12.8, 5.8, 6.5, "0.1 A 30 VDC SPDT subminiature signal switch; D2F without -01 is the 3 A version"), ("switch", "Microswitch", "SS-5GL", "Omron", 19.8, 6.4, 10.2, "5 A SPDT with lever"), ("switch", "Microswitch", "V-15-1A5", "Omron", 27.8, 10.3, 15.9, "15 A SPDT basic switch"), ("switch", "Microswitch", "KW12-3", "Generic", 12.8, 5.8, 6.5, "Lever limit switch, 3D-printer endstop type"),
        ("switch", "Slide or toggle switch", "SS12D00G3", "C&K", 8.5, 3.7, 3.7, "SPDT slide switch"), ("switch", "Slide or toggle switch", "EG1218", "E-Switch", 11.6, 4, 5, "SPDT slide switch"), ("switch", "Slide or toggle switch", "KCD1-101", "Generic", 21, 15, 20, "Panel rocker switch, 6 A 250 V"), ("switch", "Slide or toggle switch", "MTS-102", "Generic", 13, 8, 28, "Panel toggle SPDT, 6 mm mount"), ("switch", "Slide or toggle switch", "R13-502", "Generic", 16, 16, 28, "12 mm momentary panel push button"), ("switch", "Slide or toggle switch", "A6H-4102", "Omron", 12, 6, 2.6, "4 way DIP switch"),
        ("switch", "Rotary encoder", "EC11E15244G1", "ALPS Alpine", 12, 12, 19, "15 detent incremental encoder with switch"), ("switch", "Rotary encoder", "PEC11R-4215F-S0024", "Bourns", 12, 13, 26, "24 detent incremental encoder with switch"), ("switch", "Rotary encoder", "AMT102-V", "CUI / Same Sky", 28.6, 28.6, 11.5, "Capacitive quadrature shaft encoder"), ("switch", "Rotary encoder", "E6B2-CWZ6C", "Omron", 40, 40, 39, "600 PPR industrial encoder"),
        ("switch", "Key switch", "MX1A-L1NW", "Cherry", 15.6, 15.6, 18.5, "Cherry MX keyboard switch, plate cutout 14 mm"),
        ("relay", "PCB relay", "SRD-05VDC-SL-C", "Songle", 19.2, 15.6, 15.5, "5 V coil SPDT, 10 A 250 VAC"), ("relay", "PCB relay", "SRD-12VDC-SL-C", "Songle", 19.2, 15.6, 15.5, "12 V coil SPDT, 10 A 250 VAC"), ("relay", "PCB relay", "G5LE-1-E DC5", "Omron", 22.5, 16.5, 19, "5 V coil SPDT, 10 A"), ("relay", "PCB relay", "G5V-1-DC5", "Omron", 12.5, 7.5, 10, "5 V coil SPDT, signal relay"), ("relay", "PCB relay", "G6K-2F-Y DC5", "Omron", 10, 6.5, 5.2, "5 V coil DPDT, SMD signal relay"), ("relay", "PCB relay", "HF115F/005-1ZS3", "Hongfa", 29, 12.7, 15.7, "5 V coil SPDT, 16 A slim"), ("relay", "PCB relay", "G3MB-202P DC5", "Omron", 24.5, 5.1, 20.5, "5 V input, 2 A AC solid-state"), ("relay", "PCB relay", "G2R-1-E DC12", "Omron", 29, 12.7, 15.7, "12 V coil SPDT, 16 A"), ("relay", "PCB relay", "FTR-LYAA005V", "Fujitsu", 28.8, 5, 15, "Slim 5 V coil, 6 A"), ("relay", "Panel relay or contactor", "FOTEK SSR-25DA", "Fotek", 62, 45, 28, "25 A DC-to-AC panel SSR"), ("relay", "Panel relay or contactor", "G2RV-SL700 DC24", "Omron", 96, 6.2, 90, "24 V DIN-rail slim relay"),
        ("indicator", "Audio transducer", "CEM-1203", "CUI / Same Sky", 12, 12, 9.5, "5 V magnetic buzzer"), ("indicator", "Audio transducer", "PKM13EPYH4000-A0", "Murata", 13, 13, 6.9, "Piezo sounder"), ("indicator", "Audio transducer", "TMB12A05", "Generic", 12, 12, 9.5, "5 V active buzzer"), ("indicator", "Display module", "SSD1306 0.96in OLED", "Generic", 27.3, 27.8, 3.7, "128 x 64 I2C OLED module"), ("indicator", "Display module", "HD44780 1602 LCD", "Generic", 80, 36, 13, "16 x 2 character LCD"), ("indicator", "Display module", "ILI9341 2.8in TFT", "Generic", 86, 50, 6, "320 x 240 SPI TFT"), ("indicator", "Display module", "TM1637 4-digit", "Generic", 42, 24, 12, "4 digit seven-segment module"),
        ("actuator", "Solenoid", "JF-0530B", "Generic", 30, 13, 16, "12 V 300 mA push-pull solenoid, 10 mm stroke"), ("actuator", "Solenoid", "ZYE1-0530Z", "Generic", 30, 13, 16, "5 V push-pull solenoid"), ("actuator", "Solenoid", "2V025-08", "AirTAC", 44, 22, 62, "24 V 2-way G1/4 valve"), ("actuator", "Solenoid", "SY3120-5LZ-M5", "SMC", 15, 49, 28, "24 V 3-port pneumatic solenoid valve"),
        ("sensor", "Sensor module", "HC-SR04", "Generic", 45, 20, 15, "Ultrasonic rangefinder, 2-400 cm"), ("sensor", "Sensor module", "HC-SR501", "Generic", 32, 24, 18, "PIR motion sensor"), ("sensor", "Sensor module", "HX711 load-cell board", "Generic", 34, 21, 3, "24 bit load-cell amplifier board"), ("sensor", "Sensor module", "DHT22", "Aosong", 15.1, 25, 7.7, "Temperature and humidity sensor"), ("sensor", "Sensor module", "SEN-MZ80", "Generic", 18, 18, 45, "NPN infrared proximity switch"), ("sensor", "Sensor module", "LJ12A3-4-Z/BX", "Generic", 12, 12, 62, "12 mm inductive proximity switch, 4 mm range"), ("sensor", "Sensor module", "TF-Luna", "Benewake", 35, 21.25, 12, "Single-point LiDAR, 8 m"), ("sensor", "Sensor module", "RPLIDAR A1M8", "SLAMTEC", 96.8, 70.3, 55, "360 degree LiDAR, 12 m"), ("sensor", "Sensor module", "YZC-133 5kg", "Generic", 80, 12.7, 12.7, "Single-point load cell"),
    )
    for category, family, mpn, maker, x, y, z, summary in rows:
        out.append(_rec(f"{category}.{_safe(family.split()[0].lower())}.{_safe(mpn)}", category, family, f"{mpn} {family}", f"{mpn}: {summary}. About {x} x {y} x {z} mm.", f"{category} {family} {mpn} {maker} {summary}", source_type="common_trade_part" if maker == "Generic" else "manufacturer_part", manufacturer=None if maker == "Generic" else maker, mpn=mpn, specs={"summary": summary}, dimensions_mm={"x": x, "y": y, "z": z}, envelope=_box(x, y, z)))
    return out


def _power() -> list[Record]:
    out: list[Record] = []
    cells = (
        ("CR1220", "coin", 12.5, 2, 3, 35), ("CR2016", "coin", 20, 1.6, 3, 90), ("CR2032", "coin", 20, 3.2, 3, 225), ("LR44", "coin", 11.6, 5.4, 1.5, 150), ("AAA", "cylinder", 10.5, 44.5, 1.5, 1000), ("AA", "cylinder", 14.5, 50.5, 1.5, 2500), ("C", "cylinder", 26.2, 50, 1.5, 7000), ("D", "cylinder", 34.2, 61.5, 1.5, 15000), ("14500", "li-ion cylinder", 14.5, 50, 3.6, 800),
        ("18650", "li-ion cylinder", 18.4, 65, 3.6, 3000), ("21700", "li-ion cylinder", 21.1, 70, 3.6, 5000), ("26650", "li-ion cylinder", 26.5, 65.5, 3.6, 5000),
    )
    for name, kind, d, h, v, mah in cells:
        out.append(_rec(f"battery.cell.{name}", "battery", "Standard battery format", f"{name} {kind} cell", f"{name}: {d} mm dia x {h} mm, {v} V nominal, about {mah} mAh.", f"battery cell {kind} {name}", source_type="standard_series", standard="IEC 60086 / format name", specs={"nominal_v": v, "typical_capacity_mah": mah}, dimensions_mm={"d": d, "h": h}, envelope={"shape": "cylinder", "d": d, "h": h}))
    for name, maker, format_, mah, amps in (("INR18650-35E", "Samsung", "18650", 3500, 8), ("INR18650-30Q", "Samsung", "18650", 3000, 15), ("INR21700-50E", "Samsung", "21700", 5000, 10), ("INR21700-P42A", "Molicel", "21700", 4200, 30), ("INR18650-MJ1", "LG", "18650", 3500, 10), ("VTC6", "Sony / Murata", "18650", 3000, 15)):
        d, h = (18.4, 65) if format_ == "18650" else (21.1, 70)
        out.append(_rec(f"battery.li_ion.{name}", "battery", "Li-ion cell (named)", f"{maker} {name} Li-ion cell", f"{maker} {name}: {format_}, {mah} mAh, about {amps} A continuous; higher ratings need temperature cutoff. Check the current datasheet and buy from an authorized seller.", f"battery li-ion lithium 18650 21700 cell {name} {maker} {format_}", manufacturer=maker, mpn=name, specs={"format": format_, "capacity_mah": mah, "max_continuous_a": amps}, dimensions_mm={"d": d, "h": h}, envelope={"shape": "cylinder", "d": d, "h": h}))
    for code, mah in (("401120", 40), ("502030", 250), ("603048", 850), ("803040", 1000), ("103450", 1800), ("105080", 3700)):
        z, x, y = float(code[:2]) / 10 if code != "103450" and code != "105080" else float(code[:2]), float(code[2:4]), float(code[4:6])
        out.append(_rec(f"battery.lipo.{code}", "battery", "LiPo pouch cell by size code", f"{code} LiPo pouch", f"{code}: about {z} x {x} x {y} mm, 3.7 V, about {mah} mAh; add protection PCB length.", f"battery lipo pouch polymer {code}", source_type="generic_series", specs={"nominal_v": 3.7, "capacity_mah": mah}, dimensions_mm={"thickness": z, "width": x, "length": y}, envelope=_box(x, y, z)))
    for mpn, v, w, x, y, z in (("LRS-35-5", 5, 35, 99, 82, 30), ("LRS-35-12", 12, 35, 99, 82, 30), ("LRS-50-12", 12, 50, 99, 82, 30), ("LRS-100-12", 12, 100, 129, 97, 30), ("LRS-100-24", 24, 108, 129, 97, 30), ("LRS-200-24", 24, 200, 215, 115, 30), ("LRS-350-24", 24, 350, 215, 115, 30), ("RS-15-5", 5, 15, 62.5, 51, 28), ("RS-25-12", 12, 25, 78, 51, 28), ("IRM-05-5", 5, 5, 33.7, 22.2, 15), ("IRM-10-5", 5, 10, 45.7, 25.4, 21), ("IRM-20-12", 12, 20, 52.4, 27.2, 24), ("HDR-30-24", 24, 30, 35, 90, 54.5), ("HDR-60-24", 24, 60, 52.5, 90, 54.5), ("MDR-60-24", 24, 60, 78, 93, 56)):
        out.append(_rec(f"power.meanwell.{mpn}", "power", "AC-DC power supply", f"Mean Well {mpn}", f"Mean Well {mpn}: {v} V, about {w} W, {x} x {y} x {z} mm.", f"power supply psu ac-dc meanwell din enclosed {mpn} {v}v {w}w", manufacturer="Mean Well", mpn=mpn, specs={"output_v": v, "power_w": w}, dimensions_mm={"x": x, "y": y, "z": z}, envelope=_box(x, y, z)))
    for mpn, maker, summary, x, y, z in (("HLK-PM01", "Hi-Link", "5 V 3 W AC-DC module", 34, 20, 15), ("HLK-PM03", "Hi-Link", "3.3 V 3 W AC-DC module", 34, 20, 15), ("R-78E5.0-0.5", "RECOM", "5 V 0.5 A SIP-3 DC-DC", 11.6, 7.6, 10.4), ("R-78E3.3-0.5", "RECOM", "3.3 V 0.5 A SIP-3 DC-DC", 11.6, 7.6, 10.4), ("TSR 1-2450", "TRACO Power", "5 V 1 A SIP-3 DC-DC", 11.7, 7.6, 10.2), ("D24V50F5", "Pololu", "5 V 5 A buck module", 35.6, 17.8, 5.5), ("U1V10F5", "Pololu", "5 V boost module", 10.7, 15.2, 3.6)):
        out.append(_rec(f"power.module.{_safe(mpn)}", "power", "Regulator or power module", f"{maker} {mpn}", f"{mpn}: {summary}, {x} x {y} x {z} mm.", f"power module dc-dc buck boost ac-dc {mpn} {maker}", manufacturer=maker, mpn=mpn, specs={"summary": summary}, dimensions_mm={"x": x, "y": y, "z": z}, envelope=_box(x, y, z)))
    for mpn, summary, x, y, z in (("LM2596 module", "3 A adjustable buck module", 43, 21, 14), ("MT3608 module", "2 A adjustable boost module", 36, 17, 14), ("XL4015 module", "5 A adjustable buck module", 51, 26, 14), ("TP4056 module", "USB Li-ion charger with protection", 26, 17, 5), ("MP1584 module", "3 A mini buck module", 22, 17, 4), ("A4988 module", "StepStick stepper driver", 20.3, 15.2, 15), ("DRV8825 module", "StepStick stepper driver", 20.3, 15.2, 15), ("TMC2209 StepStick", "StepStick stepper driver", 20.3, 15.2, 15), ("L298N module", "Dual H-bridge board", 43, 43, 27), ("ULN2003 board", "Stepper driver board for 28BYJ-48", 35, 32, 15), ("30A BLHeli ESC", "Brushless ESC, 2-6S, about 30 A", 45, 24, 8)):
        out.append(_rec(f"power.board.{_safe(mpn)}", "power", "Common breakout or driver board", f"{mpn}", f"{mpn}: {summary}, about {x} x {y} x {z} mm. Dimensions vary by board maker.", f"module board breakout {mpn} {summary}", source_type="common_trade_part", designation=mpn, specs={"summary": summary}, dimensions_mm={"x": x, "y": y, "z": z}, envelope=_box(x, y, z)))
    boards = (("Arduino Uno R3", "Arduino", 68.6, 53.4, 15), ("Arduino Uno R4 Minima", "Arduino", 68.6, 53.4, 15), ("Arduino Nano", "Arduino", 45, 18, 8), ("Arduino Mega 2560 Rev3", "Arduino", 101.5, 53.4, 15), ("Raspberry Pi 5", "Raspberry Pi", 85, 56, 17), ("Raspberry Pi 4 Model B", "Raspberry Pi", 85, 56, 17), ("Raspberry Pi Zero 2 W", "Raspberry Pi", 65, 30, 5), ("Raspberry Pi Pico", "Raspberry Pi", 51, 21, 3.8), ("Raspberry Pi Pico 2", "Raspberry Pi", 51, 21, 3.8), ("ESP32-DevKitC", "Espressif", 54.4, 27.9, 12), ("ESP32-S3-DevKitC-1", "Espressif", 62.7, 25.4, 12), ("Teensy 4.1", "PJRC", 61, 18, 4.5), ("Teensy 4.0", "PJRC", 36, 18, 4.5), ("Seeed XIAO ESP32C3", "Seeed Studio", 21, 17.8, 3.5), ("STM32 Nucleo-64", "STMicroelectronics", 82.5, 70, 17), ("BTT SKR Mini E3 V3", "BigTreeTech", 101, 70, 15))
    for mpn, maker, x, y, z in boards:
        out.append(_rec(f"board.dev.{_safe(mpn)}", "dev_board", "Development board", f"{maker} {mpn}", f"{mpn}: about {x} x {y} x {z} mm excluding headers and connector overhang.", f"board development dev controller {mpn} {maker}", manufacturer=maker, mpn=mpn, dimensions_mm={"x": x, "y": y, "z": z}, envelope=_box(x, y, z)))
    return out


def records() -> list[Record]:
    return [*_motors(), *_connectors(), *_switching(), *_power()]
