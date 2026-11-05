"""Cross-check the component catalog and regenerate its evidence file.

Existence evidence comes from KiCad's official libraries, which name real parts:
    curl -L https://gitlab.com/kicad/libraries/kicad-symbols/-/archive/9.0.0/kicad-symbols-9.0.0.tar.gz | tar xz
    curl -L 'https://api.github.com/repos/KiCad/kicad-footprints/git/trees/master?recursive=1' -o footprints.json
    PYTHONPATH=packages/quality/src python evals/library/check_components.py kicad-symbols-9.0.0 footprints.json

A match shows a part number exists. It does not check dimensions, stock or lifecycle.
Reviewed web evidence stays in web_evidence.json beside this script and overrides a library match.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from aad_quality import components

HERE = Path(__file__).resolve().parent
OUT = Path(components.__file__).parent / "component_evidence.json"
SYMBOL_TAG = "9.0.0"


def read_symbols(folder: Path) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for path in sorted(folder.glob("*.kicad_sym")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r'\n\s*\(symbol "([^":]+)"', text):
            name = match.group(1)
            if re.search(r"_\d+_\d+$", name):
                continue
            block = text[match.end():match.end() + 3000]
            sheet = re.search(r'\(property "Datasheet" "([^"]*)"', block)
            found.setdefault(name, {"library": path.stem, "datasheet": sheet.group(1) if sheet else ""})
    return found


def match_symbol(record: dict, symbols: dict) -> str | None:
    wanted = [record.get("kicad_symbol"), record.get("mpn")]
    for name in wanted:
        if name and name in symbols:
            return name
    mpn = record.get("mpn") or ""
    if len(mpn) < 5:
        return None
    # KiCad often appends an ordering suffix: LM2940 -> LM2940-5.0_TO220.
    suffixed = sorted(s for s in symbols if s.startswith(mpn) and not s[len(mpn):len(mpn) + 1].isdigit())
    return suffixed[0] if suffixed else None


def match_footprint(record: dict, footprints: list[str]) -> str | None:
    mpn = record.get("mpn") or ""
    if len(mpn) < 5:
        return None
    return next((path for path in footprints if mpn in path), None)


def cross_check_bearings(records: dict) -> list[str]:
    tables = {}
    for record in records.values():
        if record["id"].startswith("bearing.deep_groove."):
            dims = record["dimensions_mm"]
            tables[record["designation"].split("-")[0].replace("E2.", "")] = (dims["bore_d"], dims["outer_D"], dims["width_B"])
    conflicts = []
    for record in records.values():
        if record["id"].startswith("bearing.radial."):
            dims = record["dimensions_mm"]
            mine = (dims["bore_d"], dims["outer_D"], dims["width_B"])
            theirs = tables.get(record["designation"])
            if theirs and theirs != mine:
                conflicts.append(f"{record['id']}: catalog {mine} vs table {theirs}")
    return conflicts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", type=Path)
    parser.add_argument("footprints", type=Path)
    args = parser.parse_args()
    OUT.write_text("{}\n", encoding="utf-8")
    records = {r["id"]: r for r in components.list_components() if r["id"] != components.INDEX_ID}
    symbols = read_symbols(args.symbols)
    footprints = [e["path"] for e in json.loads(args.footprints.read_text())["tree"] if e["path"].endswith(".kicad_mod")]
    web = json.loads((HERE / "web_evidence.json").read_text(encoding="utf-8"))
    evidence, unmatched = {}, []
    for id_, record in records.items():
        if id_ in web:
            evidence[id_] = {"status": "web_checked", **web[id_]}
        elif not record.get("mpn"):
            continue
        elif (symbol := match_symbol(record, symbols)):
            entry = symbols[symbol]
            evidence[id_] = {"status": "symbol_library", "source": f"KiCad symbols {SYMBOL_TAG}: {entry['library']}:{symbol}"}
            if entry["datasheet"].startswith("http"):
                evidence[id_]["datasheet_url"] = entry["datasheet"]
        elif (path := match_footprint(record, footprints)):
            evidence[id_] = {"status": "footprint_library", "source": f"KiCad footprints: {path}"}
        elif record.get("source_type") == "manufacturer_part" or record.get("mpn"):
            unmatched.append(f"{id_} ({record.get('mpn')})")
    unknown = sorted(set(web) - set(records))
    if unknown:
        raise SystemExit(f"web_evidence.json names unknown ids: {unknown}")
    OUT.write_text(json.dumps(evidence, indent=0, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for item in evidence.values():
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    print("evidence:", counts)
    print("bearing conflicts:", *(cross_check_bearings(records) or ["none"]), sep="\n  ")
    print(f"unmatched part numbers ({len(unmatched)}):", *unmatched, sep="\n  ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
