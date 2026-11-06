"""Prepare anonymous, section-view image packets and private assignment keys."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import struct
import zlib
from pathlib import Path
from typing import Any, Callable

from .corpus_loader import load_corpus, tasks_for_split

_REVIEWER_ID = re.compile(r"^[a-zA-Z0-9_-]{1,48}$")
_SECTION_TERMS = ("section", "cutaway", "interior")
_EXTERIOR_TERMS = ("front", "back", "side", "top", "bottom", "iso", "exterior", "overall")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"sRGB", b"gAMA", b"cHRM", b"bKGD", b"tRNS"}
_MAX_PNG_BYTES = 64 * 1024 * 1024
_MAX_DIMENSION = 8192


class PacketError(ValueError):
    """A source render packet is incomplete, unsafe, or not blindable."""


def _json_write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sanitize_png(data: bytes) -> bytes:
    """Drop textual/time/unknown ancillary PNG chunks without resampling pixels."""
    if len(data) > _MAX_PNG_BYTES or not data.startswith(_PNG_SIGNATURE):
        raise PacketError("review images must be PNG files no larger than 64 MiB")
    offset = len(_PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    saw_header = saw_data = saw_end = False
    width = height = 0
    while offset < len(data):
        if offset + 12 > len(data):
            raise PacketError("truncated PNG chunk")
        length = struct.unpack_from(">I", data, offset)[0]
        chunk_type = data[offset + 4 : offset + 8]
        payload_start = offset + 8
        payload_end = payload_start + length
        crc_end = payload_end + 4
        if payload_end > len(data) or crc_end > len(data):
            raise PacketError("PNG chunk length exceeds file size")
        payload = data[payload_start:payload_end]
        expected_crc = struct.unpack_from(">I", data, payload_end)[0]
        actual_crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise PacketError("PNG chunk CRC mismatch")
        if not chunk_type.isalpha():
            raise PacketError("malformed PNG chunk type")
        if not saw_header:
            if chunk_type != b"IHDR" or length != 13:
                raise PacketError("PNG must start with a 13-byte IHDR")
            width, height = struct.unpack_from(">II", payload)
            if not 0 < width <= _MAX_DIMENSION or not 0 < height <= _MAX_DIMENSION:
                raise PacketError("PNG dimensions must be between 1 and 8192 pixels")
            saw_header = True
        elif chunk_type == b"IHDR":
            raise PacketError("PNG has more than one IHDR")
        if chunk_type == b"IDAT":
            saw_data = True
        if chunk_type == b"IEND":
            if length != 0 or crc_end != len(data):
                raise PacketError("PNG must end with an empty IEND chunk")
            saw_end = True
        # Reject unknown critical chunks. Preserve only well-known color/display
        # chunks and pixel-bearing chunks; remove all other ancillary metadata.
        if chunk_type not in _PNG_KEEP and (chunk_type[0] & 0x20) == 0:
            raise PacketError(f"unsupported critical PNG chunk: {chunk_type.decode('ascii', 'replace')}")
        if chunk_type in _PNG_KEEP:
            chunks.append((chunk_type, payload))
        offset = crc_end
        if saw_end:
            break
    if not saw_header or not saw_data or not saw_end:
        raise PacketError("PNG must include IHDR, pixel data, and IEND")
    result = bytearray(_PNG_SIGNATURE)
    for chunk_type, payload in chunks:
        result.extend(struct.pack(">I", len(payload)))
        result.extend(chunk_type)
        result.extend(payload)
        result.extend(struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF))
    return bytes(result)


def _source_images(root: Path, task_id: str) -> list[tuple[str, bytes]]:
    task_dir = root / task_id
    if not task_dir.is_dir():
        raise PacketError(f"missing image directory for {task_id}")
    try:
        task_dir.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise PacketError(f"{task_id} image directory escapes its input root") from exc
    entries = list(task_dir.iterdir())
    if not entries or any(path.is_symlink() or not path.is_file() for path in entries):
        raise PacketError(f"{task_id} must contain only flat regular PNG files")
    paths = sorted(entries)
    if any(path.suffix.casefold() != ".png" for path in paths):
        raise PacketError(f"{task_id} must contain only flat PNG section-view images")
    names = [path.name.casefold() for path in paths]
    section_count = sum(any(term in name for term in _SECTION_TERMS) for name in names)
    exterior_count = sum(any(term in name for term in _EXTERIOR_TERMS) for name in names)
    if section_count < 2 or exterior_count < 1:
        raise PacketError(f"{task_id} needs at least two section/cutaway/interior images and one exterior view")
    return [(path.name, sanitize_png(path.read_bytes())) for path in paths]


def _review_form(task: dict[str, Any], source_by_id: dict[str, dict[str, str]], packet_id: str) -> dict[str, Any]:
    items = []
    for item in task["checklist"]:
        items.append({
            "id": item["id"],
            "domain": item["domain"],
            "weight": item["weight"],
            "prompt": item["prompt"],
            "sources": [source_by_id[source_id] for source_id in item["source_ids"]],
            "rating": None,
            "confidence": None,
            "evidence": [],
            "rationale": "",
        })
    return {
        "schema_version": 1,
        "reviewer_id": "FILL_IN_REVIEWER_ID",
        "reviews": [{"packet_id": packet_id, "items": items}],
    }


def prepare_study(
    before_dir: Path,
    after_dir: Path,
    output_dir: Path,
    *,
    reviewers: list[str],
    split_name: str = "tuning",
    token_factory: Callable[[], str] = lambda: secrets.token_hex(8),
) -> dict[str, Any]:
    """Create a new blind study directory. Admin mappings are kept separately."""
    data, split = load_corpus()
    tasks = tasks_for_split(data, split, split_name)
    if len(reviewers) < 2 or len(set(reviewers)) != len(reviewers):
        raise PacketError("at least two distinct reviewer IDs are required for agreement statistics")
    if any(not _REVIEWER_ID.fullmatch(reviewer) for reviewer in reviewers):
        raise PacketError("reviewer IDs may contain only letters, digits, underscore and hyphen")
    before_dir, after_dir, output_dir = Path(before_dir), Path(after_dir), Path(output_dir)
    if output_dir.exists():
        raise PacketError("output directory already exists; choose a new path")
    if not before_dir.is_dir() or not after_dir.is_dir():
        raise PacketError("before and after must be existing image directories")

    # Validate every requested input before creating any output.
    images: dict[tuple[str, str], list[tuple[str, bytes]]] = {}
    for task in tasks:
        for arm, root in (("before", before_dir), ("after", after_dir)):
            images[(task["id"], arm)] = _source_images(root, task["id"])

    output_dir.mkdir(parents=True)
    packets_root = output_dir / "review_packets"
    forms_root = output_dir / "review_forms"
    admin_root = output_dir / "restricted"
    materials_root = output_dir / "review_materials"
    for directory in (packets_root, forms_root, admin_root, materials_root):
        directory.mkdir()

    source_by_id = {source["id"]: source for source in data["sources"]}
    assignment_map: dict[str, Any] = {
        "schema_version": 1,
        "corpus_id": data["corpus_id"],
        "split": split_name,
        "tasks_sha256": split["tasks_sha256"],
        "reviewers": {},
    }
    source_digests: dict[str, str] = {}
    for reviewer in reviewers:
        reviewer_packet_root = packets_root / reviewer
        reviewer_packet_root.mkdir()
        form: dict[str, Any] = {"schema_version": 1, "reviewer_id": reviewer, "reviews": []}
        assignments = []
        entries = [(task, arm) for task in tasks for arm in ("before", "after")]
        # Each reviewer gets an independent random order and unrelated packet IDs.
        for index in range(len(entries) - 1, 0, -1):
            swap = secrets.randbelow(index + 1)
            entries[index], entries[swap] = entries[swap], entries[index]
        for task, arm in entries:
            packet_id = token_factory()
            if not isinstance(packet_id, str) or not re.fullmatch(r"[a-f0-9]{16,32}", packet_id):
                raise PacketError("packet token factory must return 16-32 lowercase hex characters")
            packet_dir = reviewer_packet_root / packet_id
            packet_dir.mkdir()
            (packet_dir / "request.txt").write_text(task["request"] + "\n", encoding="utf-8")
            selected = images[(task["id"], arm)]
            for image_index, (original_name, content) in enumerate(selected, start=1):
                blind_name = f"view-{image_index:02d}.png"
                (packet_dir / blind_name).write_bytes(content)
                source_digests[f"{reviewer}/{packet_id}/{blind_name}"] = hashlib.sha256(content).hexdigest()
            form["reviews"].append(_review_form(task, source_by_id, packet_id)["reviews"][0])
            assignments.append({"packet_id": packet_id, "task_id": task["id"], "arm": arm})
        assignment_map["reviewers"][reviewer] = assignments
        form["reviewer_id"] = reviewer
        _json_write(forms_root / f"{reviewer}.json", form)
    _json_write(admin_root / "assignment-map.json", assignment_map)
    _json_write(admin_root / "sanitized-image-sha256.json", source_digests)

    rubric_path = Path(__file__).resolve().parent / "RUBRIC.md"
    (materials_root / "RUBRIC.md").write_text(rubric_path.read_text(encoding="utf-8"), encoding="utf-8")
    _json_write(materials_root / "task-checklists.json", {
        task["id"]: {"title": task["title"], "checklist": task["checklist"]}
        for task in tasks
    })
    summary = {
        "reviewers": reviewers,
        "split": split_name,
        "task_ids": [task["id"] for task in tasks],
        "packet_count_per_reviewer": len(tasks) * 2,
        "output": str(output_dir.resolve()),
        "corpus_sha256": split["tasks_sha256"],
    }
    _json_write(output_dir / "study.json", summary)
    return summary
