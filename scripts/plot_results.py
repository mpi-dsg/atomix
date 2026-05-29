#!/usr/bin/env python3
"""Plot Atomix experiment metrics (line/bar charts).

Reads a metrics JSON (from `compute_metrics.py` or similar) with a mapping
`mode -> {metric: value}` and renders simple line plots for selected metrics.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List

try:
    import matplotlib.pyplot as plt  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "matplotlib is required; install with `pip install .[plots]`"
    ) from exc


def _load_summary(path: Path) -> Dict[str, Dict[str, float]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Expected metrics JSON object keyed by mode")
    return {
        str(mode): metrics
        for mode, metrics in data.items()
        if isinstance(metrics, dict)
    }


def _ordered_modes(modes: Iterable[str]) -> List[str]:
    preferred = [
        "Tx-Full",
        "No-Frontier",
        "No-Tx",
        "BaselineA",
        "BaselineB",
        "Saga",
        "Atomix",
    ]
    remaining = [m for m in modes if m not in preferred]
    return [m for m in preferred if m in modes] + sorted(remaining)


def plot_metric(
    summary: Dict[str, Dict[str, float]], metric: str, output: Path | None = None
) -> None:
    modes = _ordered_modes(summary.keys())
    values = [summary[mode].get(metric, 0.0) for mode in modes]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(modes, values, marker="o", linestyle="-", label=metric)
    ax.set_ylabel(metric.replace("_", " ").title())
    ax.set_xlabel("Mode")
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend()
    fig.tight_layout()

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output)
    else:
        plt.show()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", required=True, help="Path to metrics JSON (mode -> metrics)"
    )
    parser.add_argument("--metric", default="success_rate", help="Metric key to plot")
    parser.add_argument(
        "--output", help="Optional path to save PNG; if omitted, shows interactively"
    )
    args = parser.parse_args()

    summary = _load_summary(Path(args.input))
    output_path = Path(args.output) if args.output else None
    plot_metric(summary, args.metric, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
