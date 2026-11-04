# Lighter renderer experiment

This is a benchmark prototype, separate from the production `RenderBackend` implementations.
It opens no visible window. Each backend draws the same valid 30-face bracket with counterbores,
an upright hole and fillets, through front, right, top and isometric orthographic cameras.
The shared tessellation has 14,596 triangles and 66 B-rep edges. Each pane is 1024 × 1024;
the saved sheet is 2048 × 2048. This differs from the production API, whose default whole
sheet is 1024 × 1024, so timings must not be directly compared to production numbers.

From the repository root:

```sh
uv run --no-project --python 3.12 \
  --with build123d==0.13.0 --with cadquery-ocp-novtk==8.0.1.1.0 \
  --with numpy==2.5.4 --with pillow==13.0.0 --with vtk==9.7.1 \
  --with moderngl==5.13.0 --with glcontext==3.1.0 \
  python packages/render/tests/perf/backend_bench/collect.py --output /tmp/aiad-backend-bench
```

`plot.py /tmp/aiad-backend-bench` generates a timing chart using Matplotlib (installed with VTK).

`collect.py` runs imports separately and two renders per backend in fresh processes, with a
90-second timeout per process. It writes package versions and installed file sizes alongside
the raw timings and PNG hashes. Imports use warmed filesystem caches. Sheet timing starts
after imports, model construction and tessellation; it includes creating a GL context/window,
uploading geometry, four views, reading pixels, labels, and PNG compression. The OCCT backend
also performs its own native meshing. This is a small feasibility experiment, not a broad
performance study or a feature-parity test.

See [RESULTS.md](RESULTS.md) for the measured outcome and limitations.
