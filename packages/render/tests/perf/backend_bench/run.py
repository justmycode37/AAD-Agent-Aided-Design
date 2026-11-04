"""Build and render the shared bracket for one backend."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys
from time import perf_counter


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def import_backend(backend: str) -> None:
    if backend == "numpy":
        importlib.import_module("numpy")
        importlib.import_module("PIL.Image")
    elif backend == "moderngl":
        importlib.import_module("moderngl")
    elif backend == "ocp":
        from OCP import AIS, Aspect, Graphic3d, Image, OpenGl, Prs3d, V3d

        _ = (AIS, Aspect, Graphic3d, Image, OpenGl, Prs3d, V3d)
    elif backend == "vtk":
        importlib.import_module("vtkmodules.vtkCommonCore")
        importlib.import_module("vtkmodules.vtkCommonDataModel")
        importlib.import_module("vtkmodules.vtkRenderingCore")
        importlib.import_module("vtkmodules.vtkRenderingOpenGL2")
        importlib.import_module("vtkmodules.util.numpy_support")
    else:
        raise ValueError(f"unknown backend: {backend}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("numpy", "moderngl", "ocp", "vtk"))
    parser.add_argument("--probe-import", choices=("numpy", "moderngl", "ocp", "vtk"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.probe_import:
        started = perf_counter()
        import_backend(args.probe_import)
        print(json.dumps({"backend": args.probe_import, "import_ms": round((perf_counter() - started) * 1000, 2)}))
        return 0

    if not args.backend or not args.output:
        parser.error("--backend and --output are required unless --probe-import is used")

    from common import build_bracket, extract_mesh

    part = build_bracket()
    mesh = extract_mesh(part)
    if args.backend == "ocp":
        renderer = importlib.import_module("ocp_renderer")
        render = lambda: renderer.render(mesh, args.output, part)
    else:
        renderer = importlib.import_module(f"{args.backend}_renderer")
        render = lambda: renderer.render(mesh, args.output)

    # Imports are measured separately; leave only warm-process scene setup and drawing on the clock.
    import_backend(args.backend)
    started = perf_counter()
    digest, image_bytes = render()
    sheet_ms = (perf_counter() - started) * 1000.0
    print(
        json.dumps(
            {
                "backend": args.backend,
                "python": sys.version.split()[0],
                "model_faces": len(part.faces()),
                "mesh_faces": mesh.face_count,
                "triangles": len(mesh.triangles),
                "unique_edges": len(mesh.edges),
                "sheet_ms": round(sheet_ms, 2),
                "sha256": digest,
                "image_bytes": image_bytes,
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

