# Atomix

Atomix is a Python runtime for transactional LLM tool use. It tracks tool
effects, aligns commits with per-resource frontiers, and supports deterministic
replay so agent workflows can be tested under failures, contention, and
speculation.

The project is currently an alpha research implementation. The core runtime is
under `src/atomix/`; experiment runners and workload adapters are included for
reproducibility.

## Features

- Transactional effect recording with commit, abort, and compensation paths.
- Per-resource frontier tracking for ordered commits.
- Deterministic replay and serializability checking.
- Workload harnesses for WebArena, OSWorld, SWE-bench, and tau2-bench style
  experiments.
- Scripted microbenchmarks for contention, speculation, irreversible effects,
  and ablation studies.

## Requirements

- Python 3.10 or newer.
- `uv` is recommended for reproducible development installs.
- Docker is required only for WebArena and other containerized workload runs.
- API keys are required only for real LLM experiments. See `.env.example`.

## Installation

Clone the repository with submodules:

```bash
git clone --recurse-submodules https://github.com/mpi-dsg/atomix.git
cd atomix
```

Install for local development:

```bash
uv sync --extra dev --extra langgraph --extra plots
```

Or use `pip`:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[dev,langgraph,plots]'
```

For the full experiment environment, including optional LLM, sink, OSWorld,
WebArena, and SWE-bench dependencies:

```bash
python -m pip install -r requirements.txt
```

## Quickstart

Run the built-in demo:

```bash
uv run atomix demo
```

Run the test and coverage gate:

```bash
uv run --extra dev pytest --cov=atomix --cov-report=term-missing
```

Run a local experiment bundle that does not require external benchmark data:

```bash
uv run --extra dev bash scripts/run_experiments_bundle.sh v3-local
```

Run a single dispatch config:

```bash
uv run --extra dev python scripts/run_experiment_dispatch.py \
  --config configs/experiments/e4_frontier_ablation.json \
  --output results/experiments/e4_frontier_ablation.json
```

Aggregate and plot experiment results:

```bash
uv run --extra dev --extra plots python scripts/aggregate_experiment_results.py \
  --input-dir results/experiments \
  --output results/summary.json \
  --csv results/summary.csv

uv run --extra dev --extra plots python scripts/plot_experiment_results.py \
  --input results/summary.json \
  --output-dir results/plots
```

## Workload Data

Generated outputs and local benchmark data are intentionally excluded from git:
`data/`, `results/`, `runs/`, and `logs/`.

To prepare external workloads:

```bash
cp .env.example .env
# Edit .env as needed.
bash scripts/download_data.sh
```

WebArena services can be started with:

```bash
bash scripts/start_services.sh
```

More details are in `docs/workloads.md` and `docs/artifacts.md`.

## Project Structure

```text
src/atomix/                         Core runtime package
src/atomix/adapters/                Tool adapter interfaces and examples
src/atomix/baselines/               Baseline protocols used in experiments
src/atomix/checker/                 Serializability checker
src/atomix/integrations/            Orchestrator and workload integrations
src/atomix/oracles/                 Workload-specific state comparison logic
src/atomix/sinks/                   Side-effect sink implementations
src/atomix/speculation/             Speculation runner and substrates
configs/                            Reproducible experiment and workload configs
scripts/                            Setup, runner, aggregation, and plotting tools
tests/                              Pytest suite
docs/                               Project and workload documentation
workloads/                          External benchmark submodules or adapters
```

## Development

Use the existing `src/` layout and keep tests in `tests/`. Prefer small,
focused modules and add regression tests for behavior changes.

Before opening a change, run:

```bash
uv run --with ruff ruff check src tests
uv run --extra dev pytest --cov=atomix --cov-report=term-missing
uv build
```

The coverage threshold is configured in `pyproject.toml` and currently fails
below 80 percent. The same lint, test, coverage, and package build checks run
in GitHub Actions.

## Contributing

Contributions should include:

- A clear description of the behavior change.
- Tests for new or changed behavior.
- Documentation updates when commands, configs, or public APIs change.
- No committed secrets, local `.env` files, generated results, or benchmark
  data.

## Citations

If you find our paper or repository useful, please cite the paper:
```
@misc{mohammadi2026atomixtimelytransactionaltool,
      title={Atomix: Timely, Transactional Tool Use for Reliable Agentic Workflows}, 
      author={Bardia Mohammadi and Nearchos Potamitis and Lars Klein and Akhil Arora and Laurent Bindschaedler},
      year={2026},
      eprint={2602.14849},
      archivePrefix={arXiv},
      primaryClass={cs.LG},
      url={https://arxiv.org/abs/2602.14849}, 
}
```

## License

Atomix is licensed under the Apache License 2.0. See `LICENSE`.
