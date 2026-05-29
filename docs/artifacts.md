# Data And Artifact Handling

This repository should stay lightweight and clonable. Benchmark data,
intermediate logs, and generated result files are local artifacts, not source
files.

## Ignored Paths

These paths are intentionally ignored by git:

- `data/`: downloaded benchmark datasets and external workload checkouts.
- `results/`: generated experiment summaries, plots, and aggregate outputs.
- `runs/`: raw benchmark run outputs and per-run logs.
- `logs/`: local runtime logs.
- `.env`: local secrets and machine-specific configuration.

`results/.gitkeep` and `experiments/.gitkeep` keep empty output directories
discoverable without tracking generated data.

## Regeneration

Prepare local data:

```bash
cp .env.example .env
bash scripts/download_data.sh
```

Run local synthetic/control experiments:

```bash
uv run --extra dev bash scripts/run_experiments_bundle.sh v3-local
```

Run external workload experiments after data and services are available:

```bash
RUN_SETUP=1 bash scripts/run_experiments_bundle.sh smoke
```

Aggregate outputs:

```bash
uv run --extra dev --extra plots python scripts/aggregate_experiment_results.py \
  --input-dir results/experiments \
  --output results/summary.json \
  --csv results/summary.csv
```

## Publishing Rule

Before release, verify that no generated artifacts are tracked:

```bash
git ls-files results runs logs data
```

Only source files, configs, docs, tests, and intentional small placeholders
should appear in the release tree.
