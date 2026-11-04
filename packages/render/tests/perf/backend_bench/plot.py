"""Plot the two fresh-process sheet measurements, without implying a variance estimate."""

import json
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt


def main(folder: Path) -> None:
    rows = json.loads((folder / "measurements.json").read_text())["runs"]
    backends = ["ocp", "moderngl", "numpy", "vtk"]
    fig, ax = plt.subplots(figsize=(8, 3.6), layout="constrained")
    for index, backend in enumerate(backends):
        samples = [row["sheet_ms"] for row in rows if row["backend"] == backend and row["phase"] != "import" and "sheet_ms" in row]
        if len(samples) == 2:
            ax.plot(samples, [index, index], color="#c7d2df", linewidth=3)
            for sample, color, phase in zip(samples, ["#2563a5", "#dd6b20"], ["A", "B"]):
                ax.scatter(sample, index, color=color, s=55, label=f"Process {phase}" if index == 0 else None, zorder=3)
    ax.set_yticks(range(4), ["OCCT V3d", "ModernGL", "NumPy", "VTK"])
    ax.invert_yaxis()
    ax.set_xscale("log")
    ax.set_xlabel("Sheet render and PNG encoding (ms, log scale)")
    ax.set_title("Four views at 1024 px each · Apple M4 · two samples per backend")
    ax.grid(axis="x", alpha=0.2)
    ax.legend(loc="lower right")
    fig.savefig(folder / "timings.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
