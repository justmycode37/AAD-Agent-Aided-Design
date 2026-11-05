"""The catalog must refuse invented parts and stay honest about its evidence."""
from __future__ import annotations

import pytest

from aad_quality import library
from aad_quality.catalog import LibraryInputError
from aad_quality.components import EVIDENCE_STATUSES, check_items, get_component, list_components, make_envelope
from aad_quality.schema import load_spec
from aad_quality.spec import spec_checks
from types import SimpleNamespace

REQUIRED = {"id", "category", "family", "title", "summary", "tags", "evidence", "limitations"}


def _records():
    return [r for r in list_components() if r["id"] != "component.index"]


def test_records_are_complete_and_ids_are_unique():
    records = _records()
    assert len(records) > 2000
    assert len({r["id"] for r in records}) == len(records)
    for record in records:
        assert REQUIRED <= set(record), record["id"]
        assert record["evidence"]["status"] in EVIDENCE_STATUSES, record["id"]


def test_every_requested_elemental_category_is_covered():
    categories = {r["category"] for r in _records()}
    assert {"fastener", "bearing", "semiconductor", "passive", "motor", "connector", "seal", "spring", "sensor", "relay", "switch", "power", "battery", "mcu"} <= categories
    for mpn in ("2N7000", "BC547", "IRLZ44N", "LM358", "NE555", "ATmega328P-AU", "17HS19-2004S1", "SG90"):
        assert any(r.get("mpn") == mpn for r in _records()), mpn


def test_web_checked_records_cite_a_url_and_date():
    checked = [r for r in _records() if r["evidence"]["status"] == "web_checked"]
    assert checked
    for record in checked:
        assert record["evidence"]["url"].startswith("https://") and record["evidence"]["checked"]


@pytest.mark.parametrize("item,problem", [
    ({"id": "fastener.iso4762.M3-0.5", "length_mm": 17}, "nearest"),
    ({"id": "fastener.iso4762.M3-0.5"}, "needs length_mm"),
    ({"id": "fastener.iso4762.M3-0.5", "length_mm": 16, "colour": "red"}, "not a selectable"),
    ({"id": "passive.resistor.chip.0603", "resistance_ohm": 4250}, "not a E24/E96"),
    ({"id": "passive.resistor.chip.0603", "resistance_ohm": 2e7}, "range"),
    ({"id": "elec.opamp.LM358", "package": "TO-220"}, "not available"),
    ({"id": "motor.stepper.17HS9999"}, "not in the catalog"),
    ({"custom": "bracket"}, "reason"),
])
def test_check_rejects_parts_you_cannot_buy(item, problem):
    outcome = check_items([item])
    assert not outcome["ok"]
    assert problem in outcome["items"][0]["problems"][0]


def test_check_accepts_real_selections_and_declared_custom_parts():
    outcome = check_items([
        {"id": "fastener.iso4762.M3-0.5", "length_mm": 16, "qty": 4},
        {"id": "passive.resistor.chip.0603", "resistance_ohm": 4700},
        {"id": "passive.resistor.chip.0603", "resistance_ohm": 0},
        {"id": "passive.resistor.chip.0603", "resistance_ohm": 10000},
        {"id": "passive.capacitor.mlcc.0603", "capacitance_f": 1e-7, "voltage_v": 50},
        {"id": "elec.opamp.LM358", "package": "DIP-8"},
        {"custom": "printed bracket", "reason": "designed here"},
    ])
    assert outcome["ok"], outcome


def test_search_ranks_exact_size_first_and_pages():
    first = library("search", kind="component", query="M3 socket head")
    assert first["items"][0]["id"] == "fastener.iso4762.M3-0.5"
    assert first["items"][0]["evidence"] == "source_table"
    assert all("M30" not in item["id"] for item in first["items"])
    assert first["truncated"] and first["next_offset"] == 5
    second = library("search", kind="component", query="M3 socket head", offset=5)
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    assert all(".M3-0.5" in item["id"] for item in library("search", kind="component", query="M3x16 socket")["items"])
    by_size = [item["id"] for item in library("search", kind="component", query="bearing 8x22x7")["items"]]
    assert "bearing.radial.608" in by_size and "component.index" not in by_size
    assert "component.index" not in [i["id"] for i in library("search", kind="component", query="608 bearing")["items"]]
    assert library("search", kind="component", query="catalog index")["items"][0]["id"] == "component.index"
    assert library("search", kind="component", query="2N7000")["items"][0]["id"] == "elec.mosfet.2N7000"
    relaxed = library("search", kind="component", query="10k 0603 thickfilm zzz")
    assert relaxed["partial_match"]


def test_library_routes_check_and_index():
    index = library("get", kind="component", id="component.index")["item"]
    assert index["total_records"] == len(_records())
    assert library("check", items=[{"id": "bearing.radial.608"}])["ok"]
    assert not library("check", items=[{"id": "bearing.radial.60800"}])["ok"]
    with pytest.raises(LibraryInputError):
        library("check", items=[])
    with pytest.raises(LibraryInputError):
        library("search", items=[{"id": "x"}])
    with pytest.raises(LibraryInputError):
        library("search", offset=-1)
    assert library("search", kind="playbook", query="real parts")["items"][0]["id"] == "components.real-parts.v1"


def test_returned_records_are_defensive_copies():
    record = get_component("bearing.radial.608")
    record["dimensions_mm"]["bore_d"] = 999
    assert get_component("bearing.radial.608")["dimensions_mm"]["bore_d"] == 8


def _context(doc):
    return SimpleNamespace(spec=load_spec(doc), units="mm", process=None, material=None)


def test_spec_components_requirement_fails_invented_parts():
    good = {"units": "mm", "requirements": [{"id": "BOM", "components": [{"id": "bearing.radial.608", "qty": 2}, {"custom": "bracket", "reason": "designed here"}]}]}
    bad = {"units": "mm", "requirements": [{"id": "BOM", "components": [{"id": "fastener.iso4762.M3-0.5", "length_mm": 17}]}]}
    assert [c.status for c in spec_checks(_context(good))][0] == "pass"
    failed = spec_checks(_context(bad))[0]
    assert failed.status == "fail" and "nearest" in failed.message


@pytest.mark.parametrize("item", [{"id": "x", "custom": "y"}, {"qty": 1}, "text"])
def test_spec_rejects_malformed_component_entries(item):
    with pytest.raises(ValueError):
        load_spec({"units": "mm", "requirements": [{"id": "BOM", "components": [item]}]})


def test_every_envelope_builds_a_valid_solid_with_the_right_size():
    pytest.importorskip("build123d")
    built = 0
    for record in _records():
        if not record.get("envelope"):
            continue
        selection = {}
        for key, allowed in record.get("choices", {}).items():
            if key in {"length_mm", "length_in", "bore_mm"} and allowed:
                selection[key] = allowed[0] if isinstance(allowed, list) else 100
        if record["envelope"]["shape"] == "pin" and "length_mm" not in selection:
            selection = {}
            continue
        if record["envelope"]["shape"] in {"ring", "hex", "box"} and not any(record["envelope"].get(k) for k in ("h", "z")) and "length_mm" not in selection:
            continue
        shape = make_envelope(record["id"], **selection)
        assert shape.is_valid, record["id"]
        assert shape.volume > 0, record["id"]
        built += 1
    assert built > 1000
    box = make_envelope("fastener.iso4762.M3-0.5", length_mm=16).bounding_box()
    assert box.min.Z == pytest.approx(-16) and box.max.Z == pytest.approx(3) and box.max.X * 2 == pytest.approx(5.68)
    ring = make_envelope("bearing.radial.608")
    assert ring.volume == pytest.approx(3.14159265 * (11 ** 2 - 4 ** 2) * 7, rel=1e-6)
    nut = make_envelope("fastener.iso4032_nut.M3-0.5").bounding_box()
    assert nut.max.Y - nut.min.Y == pytest.approx(5.5, rel=1e-3) or nut.max.X - nut.min.X == pytest.approx(5.5, rel=1e-3)


def test_envelope_rejects_a_non_stock_size():
    pytest.importorskip("build123d")
    with pytest.raises(ValueError):
        make_envelope("fastener.iso4762.M3-0.5", length_mm=17)
    with pytest.raises(KeyError):
        make_envelope("fastener.invented")


@pytest.mark.parametrize("item,problem", [
    ({"id": "fastener.iso4762.M3-0.5", "length_mm": 16, "qty": 0}, "qty"),
    ({"id": "fastener.iso4762.M3-0.5", "length_mm": 16, "qty": -1}, "qty"),
    ({"id": "fastener.iso4762.M3-0.5", "length_mm": 16, "qty": "x"}, "qty"),
    ({"custom": "bracket", "reason": "ours", "qty": True}, "qty"),
    ({"id": "bogus", "custom": "x", "reason": "y"}, "not both"),
    ({"id": "fastener.iso4762.M3-0.5", "length_mm": 10**400}, "out of range"),
    ({"id": "passive.resistor.chip.0603", "resistance_ohm": 10**400}, "out of range"),
    ({"id": "fastener.asme_b18.3.0-80", "length_in": True}, "not True"),
    ({"id": "passive.resistor.chip.0603", "resistance_ohm": 4720}, "not a E24/E96"),
    ({"id": "protection.fuse.5x20mm"}, "needs current_a"),
    ({"id": "drive.belt.GT2"}, "needs width_mm"),
    ({"id": "pneumatic.push_fitting.4"}, "needs thread"),
    ({"id": "framing.extrusion.2020"}, "needs length_mm"),
    ({"id": "framing.extrusion.2020", "length_mm": 7000}, "1 to 6000"),
])
def test_check_refuses_incomplete_or_malformed_selections(item, problem):
    outcome = check_items([item])
    assert not outcome["ok"]
    assert problem in " ".join(outcome["items"][0]["problems"])


def test_cut_to_length_parts_accept_a_length_and_build():
    assert check_items([{"id": "framing.extrusion.2020", "length_mm": 500}, {"id": "shaft.linear.3", "length_mm": 100}])["ok"]
    for component_id in ("framing.extrusion.2020", "shaft.linear.3", "linear.leadscrew.Tr8x2", "linear.ballscrew.SFU1204"):
        assert make_envelope(component_id, length_mm=100).bounding_box().size.Z == pytest.approx(100)
    with pytest.raises(ValueError, match="needs length_mm"):
        make_envelope("framing.extrusion.2020")


def test_shoulder_screw_envelope_has_shoulder_then_thread():
    box = make_envelope("fastener.iso7379.M20-2.5", length_mm=40).bounding_box()
    assert box.min.Z == pytest.approx(-67) and box.max.Z == pytest.approx(16)
    assert box.size.X == pytest.approx(36)


def test_envelope_units_and_offset_shaft():
    inch = make_envelope("fastener.asme_b18.3.1_4-20", length_in=1, units="in").bounding_box()
    assert inch.min.Z == pytest.approx(-1)
    with pytest.raises(ValueError, match="units"):
        make_envelope("shaft.linear.3", length_mm=10, units="cm")
    motor = make_envelope("motor.stepper.28BYJ-48")
    shaft_top = [face.center().Y for face in motor.faces() if face.center().Z == pytest.approx(motor.bounding_box().max.Z)]
    assert shaft_top == [pytest.approx(8)]


def test_catalog_has_no_nameless_parts():
    assert all(r["id"].rsplit(".", 1)[-1] for r in _records())


def test_oversized_component_requirement_is_a_spec_issue():
    with pytest.raises(ValueError, match="at most 500"):
        load_spec({"units": "mm", "requirements": [{"id": "BOM", "components": [{"id": "x"}] * 501}]})


def test_search_ignores_boilerplate_and_respects_sizes():
    assert library("search", kind="component", query="datasheet")["total"] < 500
    first = library("search", kind="component", query="M3x16")["items"][0]["id"]
    assert first.startswith("fastener.") and ".M3-" in first
    top = library("search", kind="component", query="M3 socket head")["items"][0]["id"]
    assert ".M3.5" not in top
