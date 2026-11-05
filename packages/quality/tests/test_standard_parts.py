from __future__ import annotations

import builtins
from importlib.metadata import PackageNotFoundError

import pytest

from aad_quality.standard_parts import (
    _BEARING_SIZES,
    _FASTENER_LENGTHS_BY_SIZE,
    _THREAD_SIZES,
    get_part,
    list_parts,
    make_part,
)


FASTENER_ID = "fastener.iso4762.socket_head_cap_screw"
BEARING_ID = "bearing.deep_groove_ball.skt"
THREAD_ID = "thread.iso_metric_external"


def _forbid_optional_geometry_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__
    blocked_roots = {"OCP", "bd_warehouse", "build123d"}

    def guarded_import(name, *args, **kwargs):
        if name.split(".", 1)[0] in blocked_roots:
            raise AssertionError(
                f"unexpected geometry import during catalog lookup: {name}"
            )
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)


def _dimensions(shape) -> tuple[float, float, float]:
    box = shape.bounding_box()
    return (
        box.max.X - box.min.X,
        box.max.Y - box.min.Y,
        box.max.Z - box.min.Z,
    )


def _is_valid(shape) -> bool:
    value = shape.is_valid
    return value() if callable(value) else value


def test_catalog_lookup_is_kernel_free_and_returns_independent_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_optional_geometry_imports(monkeypatch)

    parts = list_parts()
    assert [part["id"] for part in parts][:3] == [FASTENER_ID, BEARING_ID, THREAD_ID]
    assert [part["family"] for part in parts][:3] == ["fastener", "bearing", "thread"]
    assert all(part["source"]["version"] == "0.4.0" for part in parts)
    assert all(part["source"]["license"] == "Apache-2.0" for part in parts)
    assert all(part["example"] and part["limitations"] for part in parts)

    parts[0]["source"]["version"] = "changed by caller"
    assert get_part(FASTENER_ID)["source"]["version"] == "0.4.0"
    assert get_part("not-a-registered-part") is None
    assert get_part(None) is None


def test_make_part_rejects_unknown_ids_and_parameters_before_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_optional_geometry_imports(monkeypatch)

    with pytest.raises(ValueError, match="unknown standard part id"):
        make_part("fastener.unregistered")
    with pytest.raises(TypeError, match="unsupported parameter.*unused"):
        make_part(FASTENER_ID, unused=1)


def test_missing_or_wrong_backend_fails_before_geometry_import(monkeypatch):
    import aad_quality.standard_parts as adapter
    _forbid_optional_geometry_imports(monkeypatch)
    def missing(package):
        raise PackageNotFoundError(package)
    monkeypatch.setattr(adapter, 'version', missing)
    with pytest.raises(RuntimeError, match=r'aad-quality\[parts\]'):
        make_part(FASTENER_ID)
    monkeypatch.setattr(adapter, 'version', lambda package: '0.3.0')
    with pytest.raises(RuntimeError, match='requires bd_warehouse==0.4.0'):
        make_part(FASTENER_ID)


@pytest.mark.parametrize(
    ("part_id", "bad_size"),
    [
        (FASTENER_ID, "M99-1"),
        (BEARING_ID, "M8-99-7"),
        (THREAD_ID, "M99-1"),
    ],
)
def test_make_part_rejects_unregistered_sizes_before_import(
    monkeypatch: pytest.MonkeyPatch, part_id: str, bad_size: str
) -> None:
    _forbid_optional_geometry_imports(monkeypatch)

    with pytest.raises(ValueError, match="size .* must be one of"):
        make_part(part_id, size=bad_size)


def test_catalog_size_and_length_choices_match_pinned_upstream_tables() -> None:
    pytest.importorskip("bd_warehouse")

    from bd_warehouse.bearing import SingleRowDeepGrooveBallBearing
    from bd_warehouse.fastener import SocketHeadCapScrew

    upstream_fastener_sizes = set(SocketHeadCapScrew.sizes("iso4762"))
    assert set(_FASTENER_LENGTHS_BY_SIZE).issubset(upstream_fastener_sizes)
    assert set(_THREAD_SIZES).issubset(upstream_fastener_sizes)
    assert set(_BEARING_SIZES).issubset(
        SingleRowDeepGrooveBallBearing.sizes("SKT")
    )

    nominal_lengths = SocketHeadCapScrew.nominal_length_range["iso4762"]
    for size, selected_lengths in _FASTENER_LENGTHS_BY_SIZE.items():
        row = SocketHeadCapScrew.fastener_data[size]
        shortest = float(row["iso4762:short"])
        longest = float(row["iso4762:long"])
        expected = tuple(
            value for value in nominal_lengths if shortest <= value <= longest
        )
        assert selected_lengths == expected


def test_fastener_length_must_be_nominal_for_selected_size() -> None:
    with pytest.raises(ValueError, match="nominal ISO 4762 length"):
        make_part(FASTENER_ID, size="M6-1", length=19)
    with pytest.raises(ValueError, match="positive finite number in mm"):
        make_part(FASTENER_ID, length="20")
    with pytest.raises(ValueError, match="positive finite number in mm"):
        make_part(THREAD_ID, length=float("inf"))
    with pytest.raises(ValueError, match="positive finite number in mm"):
        make_part(THREAD_ID, length=10**1000)


def test_fastener_dimensions_and_validity() -> None:
    pytest.importorskip("bd_warehouse")
    pytest.importorskip("build123d")

    screw = make_part(FASTENER_ID)
    assert _is_valid(screw)
    assert len(screw.solids()) == 1
    assert _dimensions(screw) == pytest.approx((10.22, 10.22, 26.0), abs=0.01)


@pytest.mark.parametrize('size,length', [('M3-0.5',5),('M10-1.5',16)])
def test_full_threaded_screws_remain_single_solids_for_other_sizes(size,length):
    pytest.importorskip('bd_warehouse')
    screw=make_part(FASTENER_ID,size=size,length=length)
    assert _is_valid(screw) and len(screw.solids())==1


def test_factory_does_not_silently_publish_into_a_surrounding_builder():
    pytest.importorskip('bd_warehouse')
    from build123d import BuildPart,Box
    with BuildPart() as context:
        Box(60,40,4)
        with pytest.raises(ValueError, match='outside an active builder'):
            make_part(BEARING_ID)
    assert len(context.part.solids())==1
    assert context.part.volume==pytest.approx(9600)


def test_bearing_builds_with_pinned_constructor_and_expected_major_dimensions() -> None:
    pytest.importorskip("bd_warehouse")
    pytest.importorskip("build123d")

    bearing = make_part(BEARING_ID)
    assert _is_valid(bearing)
    assert bearing.bore_diameter == 8
    assert bearing.outer_diameter == 22
    assert bearing.thickness == 7
    assert _dimensions(bearing) == pytest.approx((22.0, 22.0, 7.0), abs=0.01)


def test_thread_builds_with_pinned_constructor_and_expected_major_dimensions() -> None:
    pytest.importorskip("bd_warehouse")
    pytest.importorskip("build123d")

    thread = make_part(THREAD_ID)
    assert _is_valid(thread)
    assert thread.major_diameter == 6
    assert thread.pitch == 1
    assert thread.length == 8
    assert _dimensions(thread) == pytest.approx((6.0, 6.0, 8.0), abs=0.01)
