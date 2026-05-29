#!/usr/bin/env python3
"""Plot aggregated experiment results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

try:
    import matplotlib.pyplot as plt  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "matplotlib is required; install with `pip install .[plots]`"
    ) from exc


def _load_rows(path: Path) -> List[Dict[str, float]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows", []) if isinstance(data, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def _plot_contamination(rows: List[Dict[str, float]], output: Path | None) -> None:
    values = [row for row in rows if row.get("section") == "speculation"]
    if not values:
        return
    k_vals = [row.get("k_branches", 0) for row in values]
    rates = [row.get("transient_rate", 0.0) for row in values]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar([str(k) for k in k_vals], rates, color="#4c78a8")
    ax.set_xlabel("K branches")
    ax.set_ylabel("Transient contamination rate")
    ax.set_ylim(0, 1)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output)
    else:
        plt.show()


def _plot_violation_rate(rows: List[Dict[str, float]], output: Path | None) -> None:
    values = [row for row in rows if row.get("section") == "microbench"]
    if not values:
        return
    modes = sorted({str(row.get("mode")) for row in values})
    fig, ax = plt.subplots(figsize=(8, 4))
    for mode in modes:
        subset = [row for row in values if row.get("mode") == mode]
        xs = [row.get("n_concurrent", 0) for row in subset]
        ys = [row.get("violation_rate", 0.0) for row in subset]
        ax.plot(xs, ys, marker="o", label=mode)
    ax.set_xlabel("Concurrent transactions")
    ax.set_ylabel("Invariant violation rate")
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend()
    fig.tight_layout()
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output)
    else:
        plt.show()


def _plot_throughput(rows: List[Dict[str, float]], output: Path | None) -> None:
    values = [row for row in rows if row.get("section") == "microbench"]
    if not values:
        return
    modes = sorted({str(row.get("mode")) for row in values})
    fig, ax = plt.subplots(figsize=(8, 4))
    for mode in modes:
        subset = [row for row in values if row.get("mode") == mode]
        xs = [row.get("n_concurrent", 0) for row in subset]
        ys = [row.get("avg_throughput", 0.0) for row in subset]
        ax.plot(xs, ys, marker="o", label=mode)
    ax.set_xlabel("Concurrent transactions")
    ax.set_ylabel("Avg throughput (tx/s)")
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend()
    fig.tight_layout()
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output)
    else:
        plt.show()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Aggregated summary JSON")
    parser.add_argument("--output-dir", default="results/plots", help="Plot output dir")
    args = parser.parse_args()

    rows = _load_rows(Path(args.input))
    output_dir = Path(args.output_dir)

    _plot_contamination(rows, output_dir / "contamination.png")
    _plot_violation_rate(rows, output_dir / "violations.png")
    _plot_throughput(rows, output_dir / "throughput.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
